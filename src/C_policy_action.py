from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ProcurementConfig


def _round_to_moq(row: pd.Series, cfg: ProcurementConfig) -> float:
    usage_gap = max(0.0, cfg.target_cover_month - float(row["inventory_cover_month"]))
    required_qty = usage_gap * float(row["monthly_usage_forecast_ton"])
    min_qty = float(row["supplier_moq_ton"])
    planned_qty = float(row["planned_order_ton"])
    return float(max(required_qty, min_qty if required_qty > 0 else 0.0, planned_qty if row["moq_check"] else 0.0))


def _pick_action(row: pd.Series, cfg: ProcurementConfig) -> str:
    if row["customs_delay_risk_score"] >= 75:
        return "CHECK_CUSTOMS_RISK"
    if row["supply_risk_score"] >= 70 or row["supplier_reliability_score"] < 65:
        return "CHECK_SUPPLIER"
    if row["shortage_expected"] and row["inventory_cover_month"] <= cfg.urgent_cover_month:
        return "BUY_NOW"
    if row["price_risk_score"] >= 70 and row["fx_freight_risk_score"] >= 60:
        return "SPLIT_ORDER"
    if row["price_risk_score"] >= 72 and not row["moq_check"]:
        return "NEGOTIATE_PRICE"
    if row["total_risk_score"] >= cfg.high_risk_threshold:
        return "SPLIT_ORDER"
    if row["total_risk_score"] >= cfg.medium_risk_threshold and row["price_risk_score"] >= 65:
        return "NEGOTIATE_PRICE"
    return "WAIT"


def _reason_text(row: pd.Series) -> str:
    parts = [
        f"{row['item_name']} 기준 재고 커버 {row['inventory_cover_month']:.1f}개월",
        f"리드타임 갭 {row['lead_time_gap_month']:.1f}개월",
        f"종합 리스크 {row['total_risk_score']:.1f}점({row['risk_level']})",
    ]

    action = row["recommended_action"]
    if action == "BUY_NOW":
        parts.append("결품 가능성이 높아 즉시 발주가 필요합니다.")
    elif action == "SPLIT_ORDER":
        parts.append("가격/환율/운임 변동성이 커서 물량을 분할해 평균 단가 리스크를 낮춥니다.")
    elif action == "WAIT":
        parts.append("현재 재고와 리스크가 관리 가능한 수준이라 관망을 권장합니다.")
    elif action == "NEGOTIATE_PRICE":
        parts.append("가격 리스크가 높아 단가 협상 또는 견적 재확인이 우선입니다.")
    elif action == "CHECK_SUPPLIER":
        parts.append("공급 안정성 신호가 약해 대체 협력사와 납기 확약을 확인합니다.")
    elif action == "CHECK_CUSTOMS_RISK":
        parts.append("수입/통관 지연 리스크가 높아 선적, 서류, 통관 일정을 먼저 점검합니다.")

    if not row["moq_check"]:
        parts.append("계획 물량이 MOQ 기준에 미달하므로 묶음 발주 또는 협상이 필요합니다.")

    return " ".join(parts)


def recommend_purchase_actions(
    interpreted_df: pd.DataFrame,
    cfg: ProcurementConfig,
) -> pd.DataFrame:
    """Recommend final procurement actions and human-readable reasons."""
    out = interpreted_df.copy()
    out["recommended_action"] = out.apply(_pick_action, axis=1, cfg=cfg)
    out["recommended_order_ton"] = np.where(
        out["recommended_action"].isin(["BUY_NOW", "SPLIT_ORDER"]),
        out.apply(_round_to_moq, axis=1, cfg=cfg),
        0.0,
    ).round(0)
    out["reason_text"] = out.apply(_reason_text, axis=1)
    return out
