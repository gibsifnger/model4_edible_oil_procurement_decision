#필요구매 신호가 있으면 “사는 후보들 중에서” 고르고, 필요구매 신호가 없으면 기본적으로 observe를 고른다. 
 # 즉 선택 기준이 상황 인식(need_buy_flag) 에 따라 달라진다.
from __future__ import annotations
import pandas as pd

# 블록 1) infer_need_buy_flag wrapper
# 이 파일 안에서 쓸 need_buy_flag 계산 함수를 얇게 감싼다.
  # 실제 로직은 새로 안 만들고, B 해석층에서 정의한 need-buy 판단을 그대로 재사용한다.
    # “이 decision 장면이 지금 구매 필요 장면인지 아닌지를 B에서 정한 공통 기준으로 다시 확인한다.”
from ..B_interpret.need_signal import infer_need_buy_flag_from_context
def infer_need_buy_flag(row: pd.Series) -> int:
    return infer_need_buy_flag_from_context(row)

# 블록 2) 선택기 시작부: decision map과 status rank 정의
 # “후보를 고를 때는 일단 상태가 좋은 후보를 우선시하겠다. feasible > conditional >>> blocked 순이다.”
def select_best_candidate(
    decision_master_df: pd.DataFrame,
    robust_summary_df: pd.DataFrame,
) -> pd.DataFrame:
    decision_map = decision_master_df.set_index("decision_id")
    status_rank_map = {"feasible": 0, "conditional": 1, "blocked": 9}

    picks = []

# 블록 3) decision별로 후보 묶기 + need_buy_flag 계산
 # “한 decision에서 나온 후보들끼리만 비교하겠다. 그리고 이 장면이 구매 필요 상황이면 고르는 기준을 다르게 쓰겠다.”
    for decision_id, group in robust_summary_df.groupby("decision_id"):
        decision_row = decision_map.loc[decision_id]
        need_buy_flag = infer_need_buy_flag(decision_row)

        group = group.copy()
        # status_rank :  feasible > conditional > blocked
        group["status_rank"] = group["candidate_status"].map(status_rank_map).fillna(9)
        group["qty_rank"] = group["candidate_qty_ton"]
 # 블록 4) need_buy_flag == 1일 때: 일단 blocked 아니고 0톤 아닌 후보만 eligible로 추림
  # “지금은 사야 하는 장면이니까, 안 사는 안과 blocked 안은 일단 빼고 보겠다.”
        if need_buy_flag == 1:
            eligible = group[
                (group["candidate_status"] != "blocked")
                & (group["candidate_qty_ton"] > 0)
            ].copy()

 # 블록 5) need-buy인데 eligible이 아예 없을 때의 fallback 선택
  # “사야 하는 장면인데도 통과 가능한 매입안이 없으면, 그나마 가장 덜 망가지는 안을 고른다.”
            if eligible.empty:
                chosen = group.sort_values(
                    [
                        "status_rank", # feasible > conditional > blocked
                        "worst_case_shortage_ton", # 최악 shortage가 적을수록 우선
                        "worst_case_cost_vs_observe_pct", #관망 대비 최악 비용부담이 낮을수록 우선
                        "qty_rank", #더 작은 수량 우선
                    ],
                    ascending=[True, True, True, True],
                ).iloc[0]
    # 블록 6) need-buy이고 eligible이 있을 때: robust 후보 우선, 없으면 eligible 전체에서 선택
     # “살 수 있는 후보가 여러 개면, 먼저 모든 시나리오에서 shortage를 막는 안이 있는지 본다. 있으면 그 안들끼리만 비교한다.”
            else:
                robust_eligible = eligible[
                    eligible["robust_no_shortage_all_scenarios"] == 1
                ].copy()
                pool = robust_eligible if not robust_eligible.empty else eligible
    # 블록 7) need-buy일 때 실제 최종 정렬 기준
       # “사야 하는 장면이고, 시나리오상 shortage를 잘 막는 후보들이 있다면, 
       #  그 안에서는 상태가 좋고 비용부담이 덜한 안을 먼저 고른다.”
                chosen = pool.sort_values(
                    [
                        "status_rank",
                        "worst_case_cost_vs_observe_pct",
                        "worst_case_shortage_ton",
                        "qty_rank",
                    ],
                    ascending=[True, True, True, True],
                ).iloc[0]
        # 블록 8) need_buy_flag == 0일 때: 기본은 observe
          # “지금은 굳이 살 필요가 없는 장면이면, 기본적으로는 안 사는 안을 선택한다.”
        else:
            observe = group[group["candidate_name"] == "observe"]
            chosen = (
                observe.iloc[0]
                if not observe.empty
                else group.sort_values(["status_rank", "qty_rank"]).iloc[0]
            )
        # 블록 9) 최종 선택 결과 적재(“각 decision마다 ‘무슨 후보를 골랐는지’와, 그 후보의 최악 기준 성적표를 같이 남긴다.”)
        picks.append(
            {
                "decision_id": decision_id,
                "selected_candidate_name": chosen["candidate_name"],
                "selected_candidate_qty_ton": chosen["candidate_qty_ton"],
                "selected_candidate_status": chosen["candidate_status"],
                "selected_robust_no_shortage_all_scenarios": chosen[
                    "robust_no_shortage_all_scenarios"
                ],
                "selected_worst_case_shortage_ton": chosen[
                    "worst_case_shortage_ton"
                ],
                "selected_worst_case_cost_vs_observe_pct": chosen[
                    "worst_case_cost_vs_observe_pct"
                ],
                "selected_worst_case_min_ending_inventory_ton": chosen[
                    "worst_case_min_ending_inventory_ton"
                ],
            }
        )
# “이제 decision마다 어떤 후보를 선택했는지 1행씩 정리된 표가 나온다.”
    return pd.DataFrame(picks)