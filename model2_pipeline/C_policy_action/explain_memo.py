# C의 핵심 엔진이라기보다, 최종 선택 결과를 사람이 읽을 수 있는 설명 문장으로 바꾸는 해석/메모 생성 파일이다
from __future__ import annotations
import numpy as np
import pandas as pd


# 블록 1) _pick_first_present_int: 여러 후보 컬럼 중 먼저 존재하는 값을 고르는 보조함수
 # “설명문을 만들 때 A/B 위험 신호를 읽어야 하는데, 데이터가 어느 단계 컬럼으로 들어왔는지
 # 다를 수 있으니 가장 대표되는 걸 순서대로 집어서 읽자.”
def _pick_first_present_int(row: pd.Series, candidates: list[str], default: int = 0) -> int:
    for col in candidates:
        if col in row.index and pd.notna(row.get(col)):
            return int(row.get(col))
    return int(default)

# 블록 2) build_decision_reason: 짧은 핵심 선택 사유 만들기
 # “이 decision의 핵심만 짧게 보여주자. A/B 위험이 켜졌는지, 뭘 골랐는지, 
 # 상태가 어떤지, robust한지, 최악 shortage가 얼마나 남는지 한 줄로 요약하자.”
def build_decision_reason(row: pd.Series) -> str:
    parts = []
    if _pick_first_present_int(row, ["target_a_final_pred", "target_a_pred", "target_a_rule"], 0) == 1:
        parts.append("A-risk on")
    if _pick_first_present_int(row, ["target_b_final_pred", "target_b_pred", "target_b_rule"], 0) == 1:
        parts.append("B-risk on")

    parts.append(f"candidate={row['selected_candidate_name']}")
    parts.append(f"status={row['selected_candidate_status']}")
    parts.append(f"robust={int(row['selected_robust_no_shortage_all_scenarios'])}")
    parts.append(f"worst_shortage={row['selected_worst_case_shortage_ton']:.0f}t")
    return " | ".join(parts)

# 블록 3) build_additional_check_reason 시작부: 상세 추가 확인 사유 리스트 준비
 # “핵심 한 줄 말고, 왜 추가 확인이 필요한지 상세 경고/설명 사유를 차곡차곡 모으자.”
def build_additional_check_reason(row: pd.Series) -> str:
    reasons: list[str] = []

    selected_name = str(row.get("selected_candidate_name", ""))
    selected_status = str(row.get("selected_candidate_status", ""))
    required_status = str(row.get("required_candidate_status", ""))

# 블록 4) need_buy인데 observe를 고른 경우 사유 추가
 # “사야 하는 장면인데도 안 사는 안을 골랐다면, 그건 그냥 관망이 아니라 ‘살 수 있는 안이 없어서 관망한 것’일 수 있다.”
    if int(row.get("need_buy_flag", 0)) == 1 and selected_name == "observe":
        reasons.append("위험은 있으나 실행 가능한 비관망 후보가 없음")
        
# 블록 5) 선택 후보가 conditional일 때 상세 경고 사유 추가(“이 후보가 완전 통과는 아니고 조건부라면, 그 조건부의 이유를 명시하자.”)
    if selected_status == "conditional":
        if str(row.get("selected_arrival_timing_gate_result", "")) == "conditional":
            reasons.append("선택후보 도착 타이밍이 타이트함")
        if str(row.get("selected_working_capital_gate_result", "")) == "conditional":
            reasons.append("선택후보 운전자본 압박이 높음")
        if isinstance(row.get("selected_soft_warning_reason"), str) and row.get("selected_soft_warning_reason"):
            reasons.append(f"선택후보 주의사유: {row.get('selected_soft_warning_reason')}")
            
# 블록 6) 선택 후보가 blocked일 때 상세 실패 사유 추가
    if selected_status == "blocked":
        if isinstance(row.get("selected_hard_fail_reason"), str) and row.get("selected_hard_fail_reason"):
            reasons.append(f"선택후보 실행불가: {row.get('selected_hard_fail_reason')}")
        else:
            reasons.append("선택후보 실행불가")
            
# 블록 7) robust 실패 / worst-case shortage 남음 사유 추가
 # “이 후보를 골라도 모든 시나리오에서 완벽히 버티는 건 아니고, 최악 상황에선 shortage가 남는다는 걸 명시하자.”
    if int(row.get("selected_robust_no_shortage_all_scenarios", 0)) == 0:
        reasons.append("전 시나리오 기준 robust하지 않음")

    worst_shortage = float(row.get("selected_worst_case_shortage_ton", 0.0) or 0.0)
    if worst_shortage > 0:
        reasons.append(f"선택후보로도 worst-case shortage {worst_shortage:.0f}톤이 남음")

# 블록 8) 필요수량 후보(required candidate) 상태 설명
 # “원래 필요하다고 계산된 물량 후보가 있었는데, 그 후보가 blocked였는지 conditional이었는지도 같이 설명해주자.”
    required_qty = row.get("required_candidate_qty_ton", np.nan)
    if pd.notna(required_qty):
        if required_status == "blocked":
            hard_reason = str(row.get("required_candidate_hard_fail_reason", "")).strip()
            if hard_reason:
                reasons.append(f"필요수량 후보({float(required_qty):.0f}톤)는 실행불가: {hard_reason}")
            else:
                reasons.append(f"필요수량 후보({float(required_qty):.0f}톤)는 실행불가")
        elif required_status == "conditional":
            soft_reason = str(row.get("required_candidate_soft_warning_reason", "")).strip()
            if soft_reason:
                reasons.append(f"필요수량 후보({float(required_qty):.0f}톤)는 조건부: {soft_reason}")
            else:
                reasons.append(f"필요수량 후보({float(required_qty):.0f}톤)는 조건부")
# 블록 9) 필요수량 후보 대신 더 작은 실행가능 후보를 골랐는지 설명
 # “원래 필요한 물량은 더 컸지만, 실행 제약 때문에 더 작은 후보를 타협 선택했다는 뜻을 드러낸다.”
        selected_qty = float(row.get("selected_candidate_qty_ton", 0.0) or 0.0)
        if selected_qty > 0 and float(required_qty) > selected_qty and selected_name != "shortage_anchored":
            reasons.append("필요수량 후보 대신 더 작은 실행가능 후보를 선택함")
# 블록 10) B-risk가 켜져 있으면 비용 상방 리스크도 추가 확인 사유에 포함
# “이 decision은 shortage나 실행성만 볼 게 아니라, 비싸게 살 위험도 같이 체크해야 한다.”
    if _pick_first_present_int(row, ["target_b_final_pred", "target_b_pred", "target_b_rule"], 0) == 1:
        reasons.append("비용 상방 리스크도 함께 확인 필요")
# 블록 11) 중복 사유 제거
    deduped: list[str] = []
    seen = set()
    for reason in reasons:
        key = reason.strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
# “추가 확인이 필요한 이유들을 한 줄 설명문으로 만들어서 넘긴다.”
    return " | ".join(deduped)