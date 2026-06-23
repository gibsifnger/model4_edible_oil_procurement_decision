from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ProcurementConfig


REQUIRED_COLUMNS = [
    "as_of_month",
    "item_name",
    "current_inventory_ton",
    "monthly_usage_forecast_ton",
    "open_po_ton",
    "lead_time_month",
    "supplier_moq_ton",
    "planned_order_ton",
    "contract_price_usd_per_ton",
    "tariff_rate_pct",
    "customs_fee_krw_per_ton",
    "inland_freight_krw_per_ton",
    "warehouse_fee_krw_per_ton",
    "document_ready",
    "inbound_delay_days",
]


def _require_columns(df: pd.DataFrame) -> None:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Input data is missing required columns: {missing}")


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _inbound_status(row: pd.Series) -> str:
    delay_days = float(row.get("inbound_delay_days", 0) or 0)
    document_ready = _as_bool(row.get("document_ready", False))

    if delay_days >= 10 or not document_ready:
        return "AT_RISK"
    if delay_days >= 4:
        return "WATCH"
    return "ON_TRACK"


def build_current_purchase_state(
    market_df: pd.DataFrame,
    cfg: ProcurementConfig,
) -> pd.DataFrame:
    """Create the current procurement state for each edible oil item."""
    _require_columns(market_df)
    out = market_df.copy()
    out["as_of_month"] = pd.to_datetime(out["as_of_month"]).dt.to_period("M").dt.to_timestamp()

    usdkrw = out["usdkrw"] if "usdkrw" in out.columns else cfg.default_usdkrw
    base_cost = out["contract_price_usd_per_ton"] * usdkrw
    tariff_cost = base_cost * (out["tariff_rate_pct"] / 100)
    out["landed_cost_krw_per_ton"] = (
        base_cost
        + tariff_cost
        + out["customs_fee_krw_per_ton"]
        + out["inland_freight_krw_per_ton"]
        + out["warehouse_fee_krw_per_ton"]
    ).round(0)

    out["inbound_status"] = out.apply(_inbound_status, axis=1)
    out["at_risk_open_po_ton"] = np.where(out["inbound_status"] == "AT_RISK", out["open_po_ton"], 0.0)
    effective_open_po = np.where(
        out["inbound_status"] == "AT_RISK",
        0.0,
        np.where(out["inbound_status"] == "WATCH", out["open_po_ton"] * 0.5, out["open_po_ton"]),
    )
    out["effective_available_inventory_ton"] = (out["current_inventory_ton"] + effective_open_po).round(1)
    out["required_cover_month"] = (out["lead_time_month"] + cfg.safety_stock_month).round(2)

    usage = out["monthly_usage_forecast_ton"].replace(0, np.nan)
    out["inventory_cover_month"] = (
        out["effective_available_inventory_ton"] / usage
    ).replace([np.inf, -np.inf], np.nan)
    out["inventory_cover_month"] = out["inventory_cover_month"].fillna(0).round(2)
    out["lead_time_gap_month"] = (out["lead_time_month"] - out["inventory_cover_month"]).round(2)
    out["shortage_expected"] = out["inventory_cover_month"] < out["required_cover_month"]
    out["moq_check"] = out["planned_order_ton"] >= (out["supplier_moq_ton"] * cfg.moq_tolerance)

    return out
