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
    shortage_phrase = (
        "리드타임 대비 필요 커버에 부족합니다."
        if row["inventory_cover_month"] < row["required_cover_month"]
        else "리드타임 대비 필요 커버는 충족합니다."
    )
    moq_phrase = (
        "계획 물량이 MOQ에 미달해 묶음 발주 또는 MOQ 협상이 필요합니다."
        if not row["moq_check"]
        else "계획 물량은 MOQ 기준을 충족합니다."
    )
    second_source_phrase = (
        f"2nd Source는 {row['second_source_country']} 기준 준비되어 있습니다."
        if _as_bool(row["second_source_ready"])
        else f"2nd Source({row['second_source_country']}) 준비가 미흡해 공급선 다변화 검토가 필요합니다."
    )
    doc_phrase = (
        "서류 준비 완료"
        if _as_bool(row["document_ready"])
        else "서류 미완료"
    )

    return (
        f"{row['item_name']} 재고커버는 {row['inventory_cover_month']:.1f}개월이고 "
        f"필요 커버는 {row['required_cover_month']:.1f}개월입니다. {shortage_phrase} "
        f"Landed Cost는 {row['landed_cost_krw_per_ton']:,.0f}원/톤, 최근 부담 변화는 "
        f"{row['landed_cost_change_pct']:.1f}%입니다. "
        f"forecast market signal은 {row['market_signal']}이며 3개월 전망 "
        f"{row['forecast_price_change_3m_pct']:.1f}%, 12개월 전망 "
        f"{row['forecast_price_change_12m_pct']:.1f}%입니다. "
        f"ETD {row['etd_date']}, ETA {row['eta_date']}, 통관 예정 "
        f"{row['customs_clearance_expected_date']}, 국내 입고 예정 "
        f"{row['domestic_inbound_expected_date']}이고 {doc_phrase}, "
        f"입고 지연 예상은 {row['inbound_delay_days']}일입니다. "
        f"{second_source_phrase} {moq_phrase} "
        f"따라서 primary_action은 {row['primary_action']}, follow_up_action은 "
        f"{row['follow_up_action']}로 추천합니다."
    )


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
