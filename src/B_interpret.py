"""
[FILE PURPOSE]
- B_interpret는 A_state에서 만든 구매 상태를 위험/기회 신호로 해석하는 파일이다.
- 가격을 직접 예측하는 모델이 아니라, 외부 forecast signal과 운영 데이터를
  구매 검토용 risk score로 변환하는 계층이다.

[BUSINESS UNIT]
- 유지 품목 × 시황 × 환율/운임 × 입항/통관 × 공급선 다변화 판단.
- 원가율 방어, 수급 안정, 통관 지연, 2nd Source 준비 여부를 한 표에서 비교한다.

[INPUT]
- A_state 산출물과 demo CSV 원천 컬럼.
- inventory_cover_month, required_cover_month, shortage_expected,
  landed_cost_change_pct, forecast_price_change_3m_pct,
  forecast_price_change_12m_pct, forecast_confidence_score, market_signal,
  inbound_status, inbound_delay_days, second_source_ready, spec_approved,
  food_safety_docs_ready.

[OUTPUT]
- C_policy_action 단계로 넘길 리스크 점수 DataFrame.
- price_risk_score, fx_freight_risk_score, supply_risk_score,
  shortage_risk_score, landed_cost_risk_score, forecast_risk_score,
  inbound_risk_score, second_source_risk_score, total_risk_score, risk_level.

[현업 적용 시 교체 대상]
- demo forecast와 risk score는 AI 구매 어시스턴트, 시황 DB, 환율/운임 feed,
  공급사 평가, 통관 시스템, 품질/식품안전 문서 상태 데이터로 대체한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ProcurementConfig


def _clip_score(value: pd.Series) -> pd.Series:
    return value.clip(lower=0, upper=100).round(1)


def interpret_risk_signals(
    state_df: pd.DataFrame,
    cfg: ProcurementConfig,
) -> pd.DataFrame:
    """Convert procurement state and market inputs into explainable risk scores."""
    out = state_df.copy()

    # ============================================================
    # [BLOCK] 원천 신호 준비
    # [업무 의미] 시황, 환율, 운임, 공급사, 산지, 통관 신호를 리스크 계산 입력으로 정리한다.
    # [판단 기준] 상승률, 변동성, 공급사 신뢰도 하락, 산지/통관 위험을 각각 분리해서 본다.
    # [산출물] 이후 score 계산에 쓰는 중간 Series.
    # [수정 포인트] 현업에서는 AI forecast, Bloomberg/Reuters, 포워더, 공급사 평가 데이터를 연결한다.
    # [WHY] 리스크 점수는 단일 원인이 아니라 가격/물류/공급/통관 신호의 조합이다.
    # [ASSUMPTION] 결측값은 중립 또는 보수적 기본값으로 채운다.
    # [DESIGN LOGIC] 가격, 물류, 공급 리스크를 분리해 C단계에서 액션을 다르게 고를 수 있게 한다.
    # [DATA LINEAGE] demo CSV의 price/fx/freight/supplier/origin/customs 컬럼.
    # [REAL DATA REPLACEMENT] 원재료 시황, 환율 feed, 운임 index, 공급사 scorecard.
    # [INTERVIEW CHECK] "예측값을 대체하지 않고 구매 판단 신호로 변환한다"고 설명한다.
    # ============================================================
    price_momentum = out["price_change_3m_pct"].fillna(0)
    price_volatility = out["price_volatility_score"].fillna(0)
    fx_change = out["fx_change_1m_pct"].fillna(0)
    freight_change = out["freight_change_1m_pct"].fillna(0)
    supplier_reliability_gap = 100 - out["supplier_reliability_score"].fillna(75)
    origin_disruption = out["origin_disruption_score"].fillna(0)
    customs_delay = out["customs_delay_risk_score"].fillna(0)

    # 가격 상승과 변동성이 함께 높으면 단가 협상 또는 분할 발주 검토가 필요하다.
    out["price_risk_score"] = _clip_score((price_momentum * 4.0) + (price_volatility * 0.65) + 20)
    # 환율과 운임 부담은 계약 단가가 같아도 도착원가를 흔들 수 있으므로 별도 점수로 둔다.
    out["fx_freight_risk_score"] = _clip_score((fx_change * 5.0) + (freight_change * 4.0) + 25)
    # 공급사 신뢰도, 산지 이슈, 통관 리스크는 가격보다 납기 안정성에 직접 연결된다.
    out["supply_risk_score"] = _clip_score(
        supplier_reliability_gap * 0.45 + origin_disruption * 0.35 + customs_delay * 0.20
    )

    # ============================================================
    # [BLOCK] 결품 리스크 점수
    # [업무 의미] 리드타임+안전재고를 버틸 재고 커버가 있는지 구매 긴급도를 점수화한다.
    # [판단 기준] shortage_expected이면 기본 위험을 높이고, 부족 개월 수만큼 penalty를 더한다.
    # [산출물] shortage_risk_score.
    # [수정 포인트] 품목별 생산 중단 비용, 대체 가능성, 프로모션 수요를 penalty에 반영할 수 있다.
    # [WHY] BUY_NOW 판단은 가격보다 결품 방어가 우선인 경우가 많다.
    # [ASSUMPTION] demo는 월 사용량 기반 단순 커버 계산을 사용한다.
    # [DESIGN LOGIC] required_cover_month보다 부족한 정도가 클수록 점수를 높인다.
    # [DATA LINEAGE] shortage_expected, required_cover_month, inventory_cover_month.
    # [REAL DATA REPLACEMENT] MRP, 생산계획, 판매예측, WMS 가용재고.
    # [INTERVIEW CHECK] 결품 리스크는 수량이 아니라 시간 커버 기준이라고 설명한다.
    # ============================================================
    shortage_base = np.where(out["shortage_expected"], 65, 20)
    cover_penalty = (out["required_cover_month"] - out["inventory_cover_month"]).clip(lower=0) * 18
    lead_time_penalty = out["lead_time_gap_month"].clip(lower=0) * 12
    out["shortage_risk_score"] = _clip_score(shortage_base + cover_penalty + lead_time_penalty)

    # ============================================================
    # [BLOCK] Landed Cost 리스크 점수
    # [업무 의미] 원가율 방어 관점에서 도착원가 상승 압력을 점수화한다.
    # [판단 기준] landed_cost_change_pct, 관세율, 환율/운임 부담을 함께 반영한다.
    # [산출물] landed_cost_risk_score.
    # [수정 포인트] 실제 적용 시 보험료, 항만료, Demurrage/Detention, Incoterms를 추가한다.
    # [WHY] 원물 가격이 낮아도 환율/물류/통관비가 오르면 구매 원가는 악화된다.
    # [ASSUMPTION] demo는 도착원가 변화율을 이미 입력값으로 제공한다.
    # [DESIGN LOGIC] 도착원가 상승률에 가장 큰 가중을 두고 물류성 비용 리스크를 보정한다.
    # [DATA LINEAGE] landed_cost_change_pct, tariff_rate_pct, fx_freight_risk_score.
    # [REAL DATA REPLACEMENT] SAP 계약단가, 환율, 관세, 물류비 정산 데이터.
    # [INTERVIEW CHECK] "단가 기준 X, Landed Cost 기준 O"라고 설명한다.
    # ============================================================
    out["landed_cost_risk_score"] = _clip_score(
        out["landed_cost_change_pct"].fillna(0) * 5.0
        + out["tariff_rate_pct"].fillna(0) * 2.0
        + out["fx_freight_risk_score"] * 0.25
        + 10
    )

    # ============================================================
    # [BLOCK] Forecast signal 리스크 점수
    # [업무 의미] AI 구매 어시스턴트나 시황 전망값을 구매 실행 판단용 점수로 바꾼다.
    # [판단 기준] 3개월/12개월 전망, market_signal, forecast confidence를 반영한다.
    # [산출물] forecast_risk_score.
    # [수정 포인트] 실제 AI forecast output을 market_signal과 가격 전망 컬럼으로 연결한다.
    # [WHY] 이 모델은 가격 예측 모델이 아니라 예측값을 구매 액션으로 연결하는 계층이다.
    # [ASSUMPTION] forecast confidence가 낮으면 불확실성 penalty를 추가한다.
    # [DESIGN LOGIC] SUPPLY_STRESS, TIGHT_UPSIDE는 선제 구매 검토 쪽으로 점수를 높인다.
    # [DATA LINEAGE] market_signal, forecast_price_change_3m_pct, forecast_price_change_12m_pct.
    # [REAL DATA REPLACEMENT] AI 구매 어시스턴트, 시황 리포트, 원재료 price outlook.
    # [INTERVIEW CHECK] forecast를 대체하지 않고 실행 기준으로 변환한다고 설명한다.
    # ============================================================
    signal_map = {
        "SUPPLY_STRESS": 24,
        "TIGHT_UPSIDE": 18,
        "CUSTOMS_WATCH": 10,
        "STABLE": 0,
        "SOFTENING": -8,
    }
    market_signal_score = out["market_signal"].map(signal_map).fillna(8)
    low_confidence_penalty = (100 - out["forecast_confidence_score"].fillna(60)).clip(lower=0) * 0.25
    out["forecast_risk_score"] = _clip_score(
        out["forecast_price_change_3m_pct"].fillna(0) * 4.0
        + out["forecast_price_change_12m_pct"].fillna(0) * 2.0
        + market_signal_score
        + low_confidence_penalty
        + 20
    )

    # ============================================================
    # [BLOCK] 입항/통관 리스크 점수
    # [업무 의미] 발주보다 ETD/ETA, 서류, 통관 가능일 확인이 우선인 품목을 찾는다.
    # [판단 기준] 서류 미완료, inbound_status, 지연일, customs_delay_risk_score를 반영한다.
    # [산출물] inbound_risk_score.
    # [수정 포인트] 실제 적용 시 선적 확정 여부, BL/AWB, 검역, 보세창고 입고 상태를 추가한다.
    # [WHY] 통관 지연이 크면 BUY보다 일정/서류 확인 액션이 먼저일 수 있다.
    # [ASSUMPTION] demo에서는 document_ready와 inbound_delay_days가 핵심 신호다.
    # [DESIGN LOGIC] 문서 미비와 AT_RISK 상태에 큰 penalty를 부여한다.
    # [DATA LINEAGE] document_ready, inbound_status, inbound_delay_days, customs_delay_risk_score.
    # [REAL DATA REPLACEMENT] 포워더 tracking, 통관 시스템, 관세사 진행상태.
    # [INTERVIEW CHECK] 수입 구매에서는 구매오더보다 입항/통관 확인이 우선인 경우를 설명한다.
    # ============================================================
    document_penalty = np.where(out["document_ready"].astype(str).str.lower().isin(["true", "1", "yes"]), 0, 28)
    status_penalty = out["inbound_status"].map({"ON_TRACK": 0, "WATCH": 18, "AT_RISK": 35}).fillna(15)
    out["inbound_risk_score"] = _clip_score(
        out["inbound_delay_days"].fillna(0) * 4.0
        + customs_delay * 0.35
        + document_penalty
        + status_penalty
    )

    # ============================================================
    # [BLOCK] 2nd Source 리스크 점수
    # [업무 의미] 공급선 다변화가 준비되지 않은 품목을 가격 판단보다 먼저 드러낸다.
    # [판단 기준] second_source_ready, spec_approved, food_safety_docs_ready, 산지 리스크를 반영한다.
    # [산출물] second_source_risk_score.
    # [수정 포인트] 실제 적용 시 샘플 승인, 협력사 audit, 원산지별 MOQ/lead time을 추가한다.
    # [WHY] 대체 공급선이 없으면 가격보다 공급 안정성 확인이 우선 액션이 된다.
    # [ASSUMPTION] demo는 준비 여부를 Boolean 컬럼으로 단순화한다.
    # [DESIGN LOGIC] 2nd Source 미준비, 규격 미승인, 식품안전 서류 미비에 penalty를 준다.
    # [DATA LINEAGE] second_source_ready, spec_approved, food_safety_docs_ready, origin_disruption_score.
    # [REAL DATA REPLACEMENT] 공급사 master, QA 승인 상태, 식품안전 문서 관리 시스템.
    # [INTERVIEW CHECK] 공급선 다변화가 구매 리스크 관리의 일부임을 설명한다.
    # ============================================================
    second_source_penalty = np.where(
        out["second_source_ready"].astype(str).str.lower().isin(["true", "1", "yes"]),
        0,
        35,
    )
    spec_penalty = np.where(out["spec_approved"].astype(str).str.lower().isin(["true", "1", "yes"]), 0, 25)
    safety_doc_penalty = np.where(
        out["food_safety_docs_ready"].astype(str).str.lower().isin(["true", "1", "yes"]),
        0,
        20,
    )
    out["second_source_risk_score"] = _clip_score(
        second_source_penalty + spec_penalty + safety_doc_penalty + origin_disruption * 0.30
    )

    # ============================================================
    # [BLOCK] 종합 리스크와 등급
    # [업무 의미] 구매 회의에서 품목별 우선순위를 비교할 수 있게 단일 점수와 등급을 만든다.
    # [판단 기준] 가격, 환율/운임, 공급, 결품, Landed Cost, forecast, inbound, 2nd Source를 가중합한다.
    # [산출물] total_risk_score, risk_level.
    # [수정 포인트] 실제 적용 시 품목 중요도나 생산 영향도를 가중치에 반영한다.
    # [WHY] 여러 리스크를 각각 보되, 최종 회의에서는 우선순위 점수가 필요하다.
    # [ASSUMPTION] demo 가중치는 포트폴리오용 업무 가정이다.
    # [DESIGN LOGIC] 결품과 Landed Cost를 비교적 높게 보고, 다른 리스크를 보정값으로 둔다.
    # [DATA LINEAGE] 위에서 만든 모든 risk_score 컬럼.
    # [REAL DATA REPLACEMENT] 구매 KPI, 원가율 영향, 생산중단 비용으로 가중치 보정.
    # [INTERVIEW CHECK] total_risk_score는 예측값이 아니라 구매 검토 우선순위 점수라고 설명한다.
    # ============================================================
    out["total_risk_score"] = _clip_score(
        out["price_risk_score"] * 0.12
        + out["fx_freight_risk_score"] * 0.10
        + out["supply_risk_score"] * 0.13
        + out["shortage_risk_score"] * 0.20
        + out["landed_cost_risk_score"] * 0.15
        + out["forecast_risk_score"] * 0.12
        + out["inbound_risk_score"] * 0.10
        + out["second_source_risk_score"] * 0.08
    )

    # HIGH/MEDIUM/LOW는 구매 회의에서 즉시 액션, 모니터링, 관망 대상을 빠르게 나누기 위한 구간이다.
    out["risk_level"] = np.select(
        [
            out["total_risk_score"] >= cfg.high_risk_threshold,
            out["total_risk_score"] >= cfg.medium_risk_threshold,
        ],
        ["HIGH", "MEDIUM"],
        default="LOW",
    )

    return out
