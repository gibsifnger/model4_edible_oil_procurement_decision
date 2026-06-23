
# selector.py가 “어떤 후보를 고를지” 결정하는 파일이었다면,
# 이 파일은 “그 선택 결과를 회사 액션 언어로 번역하는 파일”**이다. 
# 즉 후보 선택 결과 → 최종 액션(선매입 검토 / 관망 / 추가확인) 으로 바꾸는 단계다
from __future__ import annotations
import numpy as np
import pandas as pd

# 블록 1) import + _resolve_need_buy_flag wrapper
 # “이 decision이 지금 사야 하는 장면인지 먼저 기존 공통 기준으로 확인하고, 설명문도 기존 포맷을 재사용하자.”
from ..B_interpret.need_signal import infer_need_buy_flag_from_context
from .explain_memo import build_decision_reason, build_additional_check_reason

def _resolve_need_buy_flag(row: pd.Series) -> int:
    return infer_need_buy_flag_from_context(row)

# 블록 2) 최종 action 매핑 함수 시작 + best candidate 붙이기
 # “원래 장면 정보와 최종 선택 후보를 한 표로 붙여놓고, 이 장면이 정말 구매 필요 장면인지 다시 확인하자.”
def map_final_action(decision_master_df: pd.DataFrame, best_candidate_df: pd.DataFrame) -> pd.DataFrame:
    final_df = decision_master_df.merge(best_candidate_df, on="decision_id", how="left").copy()
    final_df["need_buy_flag"] = final_df.apply(_resolve_need_buy_flag, axis=1)

# 블록 3) final_action 조건 정의
# “구매가 필요한 장면이고, 실제로 사는 후보를 골랐고, 
# 그 후보가 최소한 실행 가능하며, 전 시나리오 shortage-free까지 만족하면 ‘선매입 검토’로 올린다. 
# 반대로 굳이 살 필요도 없고 실제로 관망안을 골랐으면 ‘관망’이다. 그 사이 애매한 건 다 ‘추가확인’이다.”
    conditions = [
        (final_df["need_buy_flag"] == 1)
        & (final_df["selected_candidate_name"] != "observe")
        & (final_df["selected_candidate_status"].isin(["feasible", "conditional"]))
        & (final_df["selected_robust_no_shortage_all_scenarios"] == 1),
        (final_df["need_buy_flag"] == 0) & (final_df["selected_candidate_name"] == "observe"),
    ]
    choices = ["선매입 검토", "관망"]
    final_df["final_action"] = np.select(conditions, choices, default="추가확인")
    # 블록 4) 최종 설명문 생성
     # “최종 액션을 사람이 납득할 수 있어야 하니, 핵심 사유는 항상 붙이고, 추가확인일 때는 무엇을 더 봐야 하는지도 같이 적자.”
    final_df["final_reason"] = final_df.apply(build_decision_reason, axis=1)
    final_df["additional_check_reason"] = np.where(
        final_df["final_action"] == "추가확인",
        final_df.apply(build_additional_check_reason, axis=1),
        "",
    )
# 블록 5) 최종 출력 컬럼 정의
 # “최종 결과표에는 단순 액션만 남기지 말고, 왜 그렇게 됐는지 거슬러 올라갈 수 있는 핵심 추적 컬럼까지 같이 남기자.”
    output_cols = [
        "decision_id", "decision_month", "material_code",
        "target_a_rule", "target_b_rule",
        "target_a_pred", "target_b_pred",
        "target_a_final_pred", "target_b_final_pred",
        "need_buy_flag",
        "selected_candidate_name", "selected_candidate_qty_ton", "selected_candidate_status",
        "selected_robust_no_shortage_all_scenarios",
        "selected_worst_case_shortage_ton",
        "selected_worst_case_cost_vs_observe_pct",
        "selected_worst_case_min_ending_inventory_ton",
        "selected_hard_fail_reason", "selected_soft_warning_reason",
        "selected_working_capital_gate_result", "selected_arrival_timing_gate_result",
        "required_candidate_qty_ton", "required_candidate_status",
        "required_candidate_hard_fail_reason", "required_candidate_soft_warning_reason",
        "final_action", "final_reason", "additional_check_reason",
    ]

# 블록 6) 실제 존재하는 컬럼만 남기고 반환
 # “앞단에서 어떤 보조 컬럼이 빠졌더라도, 최종 결과표는 최대한 깨지지 않게 실제 있는 컬럼만 뽑아서 내보내자.”
    output_cols = [col for col in output_cols if col in final_df.columns]
    return final_df[output_cols]