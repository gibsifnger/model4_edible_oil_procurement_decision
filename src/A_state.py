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
]


def _require_columns(df: pd.DataFrame) -> None:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Input data is missing required columns: {missing}")


def build_current_purchase_state(
    market_df: pd.DataFrame,
    cfg: ProcurementConfig,
) -> pd.DataFrame:
    """Create the current procurement state for each edible oil item."""
    _require_columns(market_df)
    out = market_df.copy()
    out["as_of_month"] = pd.to_datetime(out["as_of_month"]).dt.to_period("M").dt.to_timestamp()

    usage = out["monthly_usage_forecast_ton"].replace(0, np.nan)
    available_inventory = out["current_inventory_ton"] + out["open_po_ton"]

    out["inventory_cover_month"] = (available_inventory / usage).replace([np.inf, -np.inf], np.nan)
    out["inventory_cover_month"] = out["inventory_cover_month"].fillna(0).round(2)
    out["lead_time_gap_month"] = (out["lead_time_month"] - out["inventory_cover_month"]).round(2)
    out["shortage_expected"] = out["inventory_cover_month"] < (
        out["lead_time_month"] + cfg.lead_time_buffer_month
    )
    out["moq_check"] = out["planned_order_ton"] >= (out["supplier_moq_ton"] * cfg.moq_tolerance)

    return out
