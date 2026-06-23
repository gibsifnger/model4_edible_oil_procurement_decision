#계산본체가 아니라 흐름 제어자임(어떤 순서로 어떤 결과로 연결하냐)
'''
0. 이 파일의 한 줄 정의
항목	              내용
파일 책임	           A/C 블록의 대표 함수를 순서대로 연결해서 최종 의사결정 trace를 만든다
입력	              decision_master_df, cfg
출력	              중간 결과 + 최종 결과를 모두 담은 dict
다음 파일로 넘기는 것	사실상 “다음 파일”이 아니라 각 단계 산출물을 다음 단계 함수에 전달
중심축	               decision row → baseline flow → candidate → gate → simulation → summary → selection → final action
'''
from __future__ import annotations #타입힌트를 나중에 평가
from typing import Dict #반환타입 명시
import pandas as pd

# 3. 블록 1 — 필요한 담당 함수들을 불러오는 부분
from .A_state.baseline_flow_builder import build_baseline_flow_df #A상태펼치기 '안샀다고 치고 앞으로 월별 흐름 어떻게 되나?
from .C_policy_action.candidate_policy import generate_candidate_df #정책후보 생성 
from .C_policy_action.scenario_summary import build_scenario_compare_summary #후보별 성적표 정리(SUMMARY/ROBUST)
from .C_policy_action.selector import select_best_candidate #최종후보선택
from .config import PipelineConfig # 이 회사의 파라미터/정책값은 무엇인가?
from .C_policy_action.action_translator import map_final_action #최종적으로 뭐라고 액션문구를 쓸것인가?
from .C_policy_action.gate_policy import apply_operating_gate #그 후보가 회사 운영상 가능한가?
from .C_policy_action.simulator import run_candidate_simulations #가능한 후보를 시나리오별로 돌리면 결과는 어떤지?


# 4. 블록 2 — 선택 후보 설명용 컬럼 정의
# “최종 선택안이 왜 그렇게 분류됐는지, MOQ/lot/창고/운전자금/도착시점 기준으로 어떤 상태였는지를 보여주는 설명용 체크리스트”
SELECTED_GATE_COLS = [
    "decision_id",
    "candidate_name",
    "candidate_status",
    "hard_fail_reason",
    "soft_warning_reason",
    "moq_gate_pass",
    "lot_multiple_gate_pass",
    "warehouse_gate_pass",
    "working_capital_gate_result",
    "arrival_timing_gate_result",
    "projected_max_end_inv_ton_base",
    "projected_total_shortage_ton_base",
]
# 5. 블록 3 — 필요한 큰 후보 설명용 컬럼 정의
#동시에 “정말 필요한 양 후보(shortage_anchored)”가 왜 못 갔는지?
SHORTAGE_ANCHORED_GATE_COLS = [
    "decision_id",
    "candidate_status",
    "hard_fail_reason",
    "soft_warning_reason",
    "candidate_qty_ton",
    "projected_total_shortage_ton_base",
]
"""
final action 단계에서 '추가확인' 사유를 설명할 수 있도록 gate context를 붙인다.
왜 선택 후보가 conditional 인지 / 왜 진짜 필요한 큰 후보는 blocked 인 를 최종 출력에서 바로 설명할 수 있다.
"""
def _attach_gate_context(best_candidate_df: pd.DataFrame, gated_candidate_df: pd.DataFrame) -> pd.DataFrame:
    out = best_candidate_df.copy() #원본을 직접 훼손하지 않고 복사본에서 작업한다.
    
    # 1) 실제 선택된 candidate의 gate 상태/사유 
    selected_gate_df = gated_candidate_df[SELECTED_GATE_COLS].copy() #원본을 직접 훼손하지 않고 복사본에서 작업한다.
    #이 rename은 단순 미관이 아니다. 최종 결과표에서 칼럼의 역할 충돌을 막는 구조화 장치다.
    selected_gate_df = selected_gate_df.rename(
        columns={
            "candidate_name": "selected_candidate_name",
            "candidate_status": "selected_candidate_gate_status",
            "hard_fail_reason": "selected_hard_fail_reason",
            "soft_warning_reason": "selected_soft_warning_reason",
            "moq_gate_pass": "selected_moq_gate_pass",
            "lot_multiple_gate_pass": "selected_lot_multiple_gate_pass",
            "warehouse_gate_pass": "selected_warehouse_gate_pass",
            "working_capital_gate_result": "selected_working_capital_gate_result",
            "arrival_timing_gate_result": "selected_arrival_timing_gate_result",
            "projected_max_end_inv_ton_base": "selected_projected_max_end_inv_ton_base",
            "projected_total_shortage_ton_base": "selected_projected_total_shortage_ton_base",
        }
    )
    out = out.merge(selected_gate_df, on=["decision_id", "selected_candidate_name"], how="left")

    #  2) shortage_anchored(필요수량 후보)의 gate 상태/사유
    anchored_gate_df = gated_candidate_df[gated_candidate_df["candidate_name"] == "shortage_anchored"][
        SHORTAGE_ANCHORED_GATE_COLS
    ].copy()
    #  #이 rename은 단순 미관이 아니다. 최종 결과표에서 칼럼의 역할 충돌을 막는 구조화 장치다.
    anchored_gate_df = anchored_gate_df.rename(
        columns={
            "candidate_status": "required_candidate_status",
            "hard_fail_reason": "required_candidate_hard_fail_reason",
            "soft_warning_reason": "required_candidate_soft_warning_reason",
            "candidate_qty_ton": "required_candidate_qty_ton",
            "projected_total_shortage_ton_base": "required_candidate_projected_total_shortage_ton_base",
        }
    )
    out = out.merge(anchored_gate_df, on="decision_id", how="left")

    return out


# 5. 블록 3 — 필요한 큰 후보 설명용 컬럼 정의
def run_full_decision_pipeline(decision_master_df: pd.DataFrame, cfg: PipelineConfig) -> Dict[str, pd.DataFrame]:
    baseline_flow_df = build_baseline_flow_df(decision_master_df, cfg) #“지금 안 샀다고 보면 앞으로 월별로 어떻게 흘러가나?”
    candidate_df = generate_candidate_df(decision_master_df, cfg) #2단계: candidate 생성
    gated_candidate_df = apply_operating_gate(decision_master_df, baseline_flow_df, candidate_df, cfg) #3단계: operating gate 적용
    simulation_result_df = run_candidate_simulations(decision_master_df, baseline_flow_df, gated_candidate_df, cfg)
       #4단계: simulation 실행
    scenario_summary_df, robust_summary_df = build_scenario_compare_summary(simulation_result_df) #5단계: scenario 비교 요약
    best_candidate_df = select_best_candidate(decision_master_df, robust_summary_df) #6단계: 최종 후보 선택
    best_candidate_df = _attach_gate_context(best_candidate_df, gated_candidate_df) #7단계: gate 설명맥락 부착
    final_decision_df = map_final_action(decision_master_df, best_candidate_df) #8단계: 최종 액션 변환

    return {
        "baseline_flow_df": baseline_flow_df,
        "candidate_df": candidate_df,
        "gated_candidate_df": gated_candidate_df,
        "simulation_result_df": simulation_result_df,
        "scenario_summary_df": scenario_summary_df,
        "robust_summary_df": robust_summary_df,
        "best_candidate_df": best_candidate_df,
        "final_decision_df": final_decision_df,
    }
