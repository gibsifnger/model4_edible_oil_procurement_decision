"""
[FILE PURPOSE]
- pipeline.py는 A_state, B_interpret, C_policy_action의 전체 흐름을 연결하는 orchestration 파일이다.
- 개별 구매 판단 로직 자체보다 데이터 흐름과 최종 산출물 컬럼 정합성을 관리한다.

[BUSINESS UNIT]
- 유지 품목 구매 판단표 생성 프로세스.
- 입력 CSV를 상태, 리스크, 액션 단계로 통과시켜 구매 회의용 summary CSV를 만든다.

[INPUT]
- data/edible_oil_market_inputs_demo.csv.
- 현재고, Open PO, 리드타임, MOQ, Landed Cost, forecast signal,
  ETD/ETA, 통관 일정, 2nd Source 준비 여부.

[OUTPUT]
- outputs/edible_oil_purchase_decision_summary.csv.
- item_name, origin_country, inventory_cover_month, required_cover_month,
  landed_cost_krw_per_ton, risk_score, primary_action, follow_up_action,
  recommended_order_ton, reason_text.

[현업 적용 시 교체 대상]
- 입력 CSV는 ERP/SAP/MM/WMS/포워더/통관/품질문서/시황 데이터마트로 대체하고,
  output CSV는 BI 리포트, 구매 검토 회의 자료, batch job 산출물로 연결한다.
"""

from __future__ import annotations

import pandas as pd

from .A_state import build_current_purchase_state
from .B_interpret import interpret_risk_signals
from .C_policy_action import recommend_purchase_actions
from .config import DEFAULT_CONFIG, DATA_PATH, OUTPUT_PATH, ProcurementConfig


SUMMARY_COLUMNS = [
    "as_of_month",
    "item_name",
    "origin_country",
    "usd_krw",
    "inventory_cover_month",
    "required_cover_month",
    "landed_cost_krw_per_ton",
    "landed_cost_risk_score",
    "forecast_risk_score",
    "inbound_risk_score",
    "second_source_risk_score",
    "total_risk_score",
    "risk_level",
    "primary_action",
    "follow_up_action",
    "recommended_action",
    "recommended_order_ton",
    "moq_status",
    "moq_shortfall_ton",
    "reason_text",
]


# ============================================================
# [BLOCK] A/B/C 파이프라인 오케스트레이션
# [업무 의미] 구매 판단표를 만들기 위해 입력, 상태 생성, 리스크 해석, 액션 추천을 순서대로 연결한다.
# [판단 기준] 파일별 책임은 유지하고, 여기서는 실행 순서와 output 컬럼만 통제한다.
# [산출물] outputs/edible_oil_purchase_decision_summary.csv.
# [수정 포인트] 현업에서는 input_path/output_path를 batch 경로나 데이터마트 경로로 교체한다.
# [WHY] A/B/C를 분리하면 상태, 리스크, 액션 설명이 명확해지고 유지보수가 쉬워진다.
# [ASSUMPTION] demo CSV는 하나의 월별 snapshot을 담고 있다.
# [DESIGN LOGIC] 상태 -> 해석 -> 액션 순서를 고정해 의사결정 trace를 보존한다.
# [DATA LINEAGE] data/edible_oil_market_inputs_demo.csv에서 output summary CSV까지 이어진다.
# [REAL DATA REPLACEMENT] ERP export, WMS snapshot, 통관/포워더 feed, 시황 API를 input으로 연결한다.
# [INTERVIEW CHECK] 이 파일은 계산보다 데이터 흐름과 산출물 표준화를 담당한다고 설명한다.
# ============================================================
def run_pipeline(
    input_path=DATA_PATH,
    output_path=OUTPUT_PATH,
    cfg: ProcurementConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    # 입력 CSV는 demo에서는 정적 파일이지만, 현업에서는 SAP/WMS/시황 데이터가 결합된 snapshot이 된다.
    market_df = pd.read_csv(input_path)
    # A_state: 현재고, Open PO, 입항 리스크를 구매 판단 가능한 현재 상태로 변환한다.
    state_df = build_current_purchase_state(market_df, cfg)
    # B_interpret: 상태값과 forecast signal을 구매 검토용 risk score로 해석한다.
    interpreted_df = interpret_risk_signals(state_df, cfg)
    # C_policy_action: risk score를 primary/follow-up action과 reason_text로 변환한다.
    action_df = recommend_purchase_actions(interpreted_df, cfg)
    # GitHub에서 CSV를 볼 때 reason_text가 셀 내부 줄바꿈으로 깨지지 않도록 한 줄로 정리한다.
    action_df["reason_text"] = (
        action_df["reason_text"]
        .astype(str)
        .str.replace(r"[\r\n]+", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    # 최종 output은 구매 회의에서 바로 볼 수 있는 핵심 컬럼만 남긴다.
    output_df = action_df[SUMMARY_COLUMNS].sort_values(
        ["total_risk_score", "item_name"],
        ascending=[False, True],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig는 Excel/Windows 환경에서 한글 품목명이 깨지지 않도록 하기 위한 저장 방식이다.
    output_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_df
