from __future__ import annotations

import pandas as pd

from .config import ProcurementConfig


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _round_to_cover_qty(row: pd.Series) -> float:
    usage_gap = max(0.0, float(row["required_cover_month"]) - float(row["inventory_cover_month"]))
    required_qty = usage_gap * float(row["monthly_usage_forecast_ton"])
    min_qty = float(row["supplier_moq_ton"])
    planned_qty = float(row["planned_order_ton"])
    if required_qty <= 0:
        return 0.0
    return float(max(required_qty, min_qty, planned_qty))


def _primary_action(row: pd.Series, cfg: ProcurementConfig) -> str:
    if row["shortage_expected"] and row["inventory_cover_month"] <= cfg.urgent_cover_month:
        return "BUY_NOW"
    if row["inbound_risk_score"] >= 75:
        return "INBOUND_SCHEDULE_CHECK"
    if row["landed_cost_risk_score"] >= 75:
        return "LANDED_COST_REVIEW"
    if row["second_source_risk_score"] >= 70:
        return "SECOND_SOURCE_REVIEW"
    if row["customs_delay_risk_score"] >= 75:
        return "CHECK_CUSTOMS_RISK"
    if row["supply_risk_score"] >= 70 or row["supplier_reliability_score"] < 65:
        return "CHECK_SUPPLIER"
    if row["forecast_risk_score"] >= 72 and row["price_risk_score"] >= 65:
        return "SPLIT_ORDER"
    if row["total_risk_score"] >= cfg.high_risk_threshold:
        return "SPLIT_ORDER"
    if row["price_risk_score"] >= 70 or row["landed_cost_change_pct"] >= 6:
        return "NEGOTIATE_PRICE"
    return "WAIT"


def _follow_up_action(row: pd.Series) -> str:
    checks = [
        ("CHECK_CUSTOMS_RISK", row["customs_delay_risk_score"] >= 70),
        ("INBOUND_SCHEDULE_CHECK", row["inbound_risk_score"] >= 60 or row["inbound_status"] != "ON_TRACK"),
        ("SECOND_SOURCE_REVIEW", row["second_source_risk_score"] >= 55),
        ("LANDED_COST_REVIEW", row["landed_cost_risk_score"] >= 60),
        ("CHECK_SUPPLIER", row["supply_risk_score"] >= 60),
        ("NEGOTIATE_PRICE", row["price_risk_score"] >= 70),
    ]
    for action, should_check in checks:
        if should_check and action != row["primary_action"]:
            return action
    return "NONE"


def _reason_text(row: pd.Series) -> str:
    cover_note = (
        "coverage below required lead-time+safety-stock level"
        if row["inventory_cover_month"] < row["required_cover_month"]
        else "coverage meets required lead-time+safety-stock level"
    )
    moq_note = (
        "MOQ shortfall: bundle order or negotiate MOQ"
        if not row["moq_check"]
        else "MOQ requirement met"
    )
    second_source_note = (
        f"2nd Source ready in {row['second_source_country']}"
        if _as_bool(row["second_source_ready"])
        else f"2nd Source not ready in {row['second_source_country']}"
    )
    doc_note = "documents ready" if _as_bool(row["document_ready"]) else "documents not ready"

    reasons = [
        f"inventory cover {row['inventory_cover_month']:.1f}M vs required cover {row['required_cover_month']:.1f}M",
        cover_note,
        f"Landed Cost {row['landed_cost_krw_per_ton']:.0f} KRW/ton with change {row['landed_cost_change_pct']:.1f}%",
        (
            f"forecast market signal {row['market_signal']} "
            f"3M {row['forecast_price_change_3m_pct']:.1f}% "
            f"12M {row['forecast_price_change_12m_pct']:.1f}%"
        ),
        (
            f"ETD {row['etd_date']} ETA {row['eta_date']} "
            f"customs {row['customs_clearance_expected_date']} "
            f"domestic inbound {row['domestic_inbound_expected_date']} "
            f"{doc_note} inbound delay {row['inbound_delay_days']}D"
        ),
        second_source_note,
        moq_note,
        f"primary_action {row['primary_action']} follow_up_action {row['follow_up_action']}",
    ]
    return " / ".join(str(reason).replace("\r", " ").replace("\n", " ").strip() for reason in reasons)


def recommend_purchase_actions(
    interpreted_df: pd.DataFrame,
    cfg: ProcurementConfig,
) -> pd.DataFrame:
    """Recommend primary and follow-up procurement actions with practical reasons."""
    out = interpreted_df.copy()
    out["primary_action"] = out.apply(_primary_action, axis=1, cfg=cfg)
    out["follow_up_action"] = out.apply(_follow_up_action, axis=1)
    out["recommended_action"] = out["primary_action"]
    out["recommended_order_ton"] = out.apply(
        lambda row: _round_to_cover_qty(row) if row["primary_action"] in {"BUY_NOW", "SPLIT_ORDER"} else 0.0,
        axis=1,
    ).round(0)
    out["reason_text"] = out.apply(_reason_text, axis=1)
    return out
