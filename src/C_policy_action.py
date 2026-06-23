"""
[FILE PURPOSE]
- C_policy_action은 B_interpret의 risk score를 실제 구매 액션으로 변환하는 파일이다.
- BUY_NOW, SPLIT_ORDER, WAIT, CHECK 계열 액션을 primary/follow-up으로 분리한다.

[BUSINESS UNIT]
- 유지 원물 구매 담당자의 발주, 분할 구매, 단가 협상, 공급사 확인,
  통관/입항 일정 확인, 2nd Source 검토 의사결정.

[INPUT]
- B_interpret 산출물.
- shortage_expected, inventory_cover_month, risk_score 계열,
  customs_delay_risk_score, supplier_reliability_score, inbound_status,
  moq_check, forecast signal, ETD/ETA, 2nd Source 상태.

[OUTPUT]
- primary_action, follow_up_action, recommended_action, recommended_order_ton, reason_text.
- recommended_action은 기존 출력 호환성을 위해 primary_action과 동일하게 유지한다.

[현업 적용 시 교체 대상]
- action 기준은 구매 정책, 생산 긴급도, 협력사 계약 조건, 통관 SLA,
  품질/식품안전 승인 프로세스에 맞춰 조정한다.
"""

from __future__ import annotations

import pandas as pd

from .config import ProcurementConfig


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


# ============================================================
# [BLOCK] 권장 발주량 계산
# [업무 의미] 결품 또는 분할 구매가 필요할 때 최소 얼마를 발주할지 산정한다.
# [판단 기준] required_cover_month까지 부족한 사용량, MOQ, 계획 발주량 중 큰 값을 사용한다.
# [산출물] recommended_order_ton 계산에 쓰는 후보 수량.
# [수정 포인트] 실제 적용 시 lot multiple, 창고 capacity, working capital 한도를 추가한다.
# [WHY] 액션만 제시하면 실행성이 낮으므로 대략적인 주문량 근거가 필요하다.
# [ASSUMPTION] demo에서는 MOQ와 계획 발주량까지만 반영한다.
# [DESIGN LOGIC] 부족 커버를 채우되 MOQ보다 작으면 공급사 최소 발주 기준을 맞춘다.
# [DATA LINEAGE] required_cover_month, inventory_cover_month, monthly_usage_forecast_ton, supplier_moq_ton.
# [REAL DATA REPLACEMENT] SAP 구매단위, MOQ, lot size, 창고 capacity, 예산 한도.
# [INTERVIEW CHECK] recommended_order_ton은 최종 PO 수량이 아니라 검토용 제안 수량이라고 설명한다.
# ============================================================
def _round_to_cover_qty(row: pd.Series) -> float:
    usage_gap = max(0.0, float(row["required_cover_month"]) - float(row["inventory_cover_month"]))
    required_qty = usage_gap * float(row["monthly_usage_forecast_ton"])
    min_qty = float(row["supplier_moq_ton"])
    planned_qty = float(row["planned_order_ton"])
    if required_qty <= 0:
        return 0.0
    return float(max(required_qty, min_qty, planned_qty))


# ============================================================
# [BLOCK] primary_action 결정
# [업무 의미] 구매 담당자가 지금 가장 먼저 취해야 할 대표 액션을 고른다.
# [판단 기준] 결품, inbound, Landed Cost, 2nd Source, 통관, 공급사, forecast, 종합 리스크 순서로 본다.
# [산출물] primary_action.
# [수정 포인트] 현업에서는 생산 중단 위험, 프로모션 일정, 협력사별 납기 확약을 추가한다.
# [WHY] 모든 리스크를 한 번에 처리할 수 없으므로 우선순위가 필요하다.
# [ASSUMPTION] demo에서는 rule-based priority로 액션을 선택한다.
# [DESIGN LOGIC] 결품 임박은 BUY_NOW, 입항/통관 문제는 CHECK, 가격/전망 부담은 SPLIT/협상으로 연결한다.
# [DATA LINEAGE] shortage_expected, risk_score, supplier_reliability_score, landed_cost_change_pct.
# [REAL DATA REPLACEMENT] 구매 회의 의사결정 룰, 품목별 escalation 기준.
# [INTERVIEW CHECK] action 분기는 모델 예측이 아니라 구매 정책 rule이라고 설명한다.
# ============================================================
def _primary_action(row: pd.Series, cfg: ProcurementConfig) -> str:
    # 결품 위험과 긴급 재고 부족이 동시에 있으면 가격보다 생산 차질 방어가 우선이므로 BUY_NOW.
    if row["shortage_expected"] and row["inventory_cover_month"] <= cfg.urgent_cover_month:
        return "BUY_NOW"
    # 입항/통관 리스크가 매우 높으면 추가 발주보다 ETD/ETA, 서류, 통관 가능일 확인이 우선.
    if row["inbound_risk_score"] >= 75:
        return "INBOUND_SCHEDULE_CHECK"
    # 도착원가 부담이 높으면 원가율 방어 관점에서 Landed Cost 구성비를 먼저 재점검.
    if row["landed_cost_risk_score"] >= 75:
        return "LANDED_COST_REVIEW"
    # 2nd Source가 준비되지 않은 품목은 가격보다 대체 공급선 확보가 우선 과제.
    if row["second_source_risk_score"] >= 70:
        return "SECOND_SOURCE_REVIEW"
    # 통관 지연 리스크가 높으면 CHECK_CUSTOMS_RISK로 선적/서류/통관 가능일을 확인.
    if row["customs_delay_risk_score"] >= 75:
        return "CHECK_CUSTOMS_RISK"
    # 공급사 신뢰도나 공급 안정성이 낮으면 협력사 납기 확약 및 대체 공급처 확인.
    if row["supply_risk_score"] >= 70 or row["supplier_reliability_score"] < 65:
        return "CHECK_SUPPLIER"
    # forecast 상승 신호와 현재 가격 리스크가 동시에 높으면 전량보다 분할 발주가 적합.
    if row["forecast_risk_score"] >= 72 and row["price_risk_score"] >= 65:
        return "SPLIT_ORDER"
    # 종합 리스크가 HIGH이면 구매 시점 분산을 위해 SPLIT_ORDER를 검토.
    if row["total_risk_score"] >= cfg.high_risk_threshold:
        return "SPLIT_ORDER"
    # 가격 또는 Landed Cost 상승 부담이 있으면 단가 협상/견적 재확인이 우선.
    if row["price_risk_score"] >= 70 or row["landed_cost_change_pct"] >= 6:
        return "NEGOTIATE_PRICE"
    # 리스크가 낮고 긴급성이 없으면 관망한다.
    return "WAIT"


# ============================================================
# [BLOCK] follow_up_action 결정
# [업무 의미] 대표 액션 외에 구매 담당자가 함께 확인해야 할 보조 액션을 고른다.
# [판단 기준] 통관, inbound, 2nd Source, Landed Cost, 공급사, 가격 리스크 순으로 점검한다.
# [산출물] follow_up_action.
# [수정 포인트] 현업에서는 담당 조직별 후속 체크리스트를 여러 개로 확장할 수 있다.
# [WHY] 실제 구매 업무는 하나의 액션만으로 끝나지 않고 후속 확인이 병행된다.
# [ASSUMPTION] demo에서는 가장 중요한 후속 액션 하나만 반환한다.
# [DESIGN LOGIC] primary_action과 중복되지 않는 첫 번째 중요 체크 항목을 선택한다.
# [DATA LINEAGE] customs_delay_risk_score, inbound_risk_score, inbound_status, second_source_risk_score.
# [REAL DATA REPLACEMENT] 통관 SLA, QA 승인, 협력사 escalation 룰.
# [INTERVIEW CHECK] primary/follow-up 분리로 실행 우선순위를 명확히 했다고 설명한다.
# ============================================================
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


# ============================================================
# [BLOCK] reason_text 생성
# [업무 의미] 추천 액션의 판단 근거를 구매 담당자가 읽을 수 있는 문장으로 만든다.
# [판단 기준] 재고커버, 리드타임 대비 부족, Landed Cost, forecast signal,
#             ETD/ETA/통관, 2nd Source, MOQ를 한 줄에 요약한다.
# [산출물] reason_text.
# [수정 포인트] 현업 보고서 문체나 BI 표시 형식에 맞춰 문구를 바꿀 수 있다.
# [WHY] 면접관이나 구매팀이 결과만 보고도 왜 그 액션인지 추적할 수 있어야 한다.
# [ASSUMPTION] GitHub CSV 가독성을 위해 줄바꿈 없이 " / " 구분자를 사용한다.
# [DESIGN LOGIC] 설명은 계산에 영향을 주지 않고, 판단 근거를 사람이 읽는 형태로 묶는다.
# [DATA LINEAGE] A/B/C 단계에서 만든 상태, 점수, 액션 컬럼 전체.
# [REAL DATA REPLACEMENT] 실제 보고서에서는 품목코드, 공급사명, PO번호, 선적번호를 추가한다.
# [INTERVIEW CHECK] reason_text는 모델 설명가능성을 위한 감사 trail이라고 설명한다.
# ============================================================
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


# ============================================================
# [BLOCK] 최종 액션 테이블 생성
# [업무 의미] risk score가 붙은 행마다 primary/follow-up action과 권장 발주량을 붙인다.
# [판단 기준] _primary_action, _follow_up_action, _round_to_cover_qty의 rule을 순서대로 적용한다.
# [산출물] C단계 결과 DataFrame.
# [수정 포인트] 실제 적용 시 action별 담당자, SLA, 승인 상태를 추가 컬럼으로 붙일 수 있다.
# [WHY] B단계 점수만으로는 실행이 어렵기 때문에 구매 업무 언어로 변환한다.
# [ASSUMPTION] recommended_action은 기존 output 호환성을 위해 primary_action과 동일하다.
# [DESIGN LOGIC] BUY_NOW/SPLIT_ORDER일 때만 권장 발주량을 계산하고, CHECK/WAIT은 0으로 둔다.
# [DATA LINEAGE] interpreted_df 전체.
# [REAL DATA REPLACEMENT] 구매 요청서, PO 생성, 협력사 확인 workflow와 연결한다.
# [INTERVIEW CHECK] 최종 산출물이 회의용 판단표라는 점을 설명한다.
# ============================================================
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
