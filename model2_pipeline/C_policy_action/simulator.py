
# 이 파일은 그 후보를 base / stress / shock 같은 세계에 넣었을 때 월별로 무슨 일이 일어나는지 계산하는 파일이다.
from __future__ import annotations
from typing import Dict, List
import pandas as pd
from ..config import PipelineConfig
from ..A_state.scenario_world import apply_scenario

# 블록 1) 함수 선언 + 시나리오 세계 적용
  # “같은 후보라도 base / stress / shock마다 수요, open PO, 도착시점, 비용이 달라질 수 있으니 먼저 그 세계로 바꿔놓고 계산한다.”
def simulate_candidate_under_scenario(
    decision_id: str,
    decision_row: pd.Series,
    flow_subset: pd.DataFrame,
    candidate_row: pd.Series,
    scenario_name: str,
    cfg: PipelineConfig,
) -> pd.DataFrame:
    flow, scenario_arrival_month_idx = apply_scenario(flow_subset, candidate_row, scenario_name, cfg)

 # 블록 2) 시작 재고 / 현재 원가 / 후보수량 / 긴급매입 프리미엄률 준비
  # “이 후보를 시나리오에 넣고 굴릴 때, 출발 재고는 얼마인지, 후보 물량은 얼마인지, 부족하면 긴급매입을 얼마나 비싸게 해야 하는지를 먼저 정한다.”
    begin_inv = float(decision_row.get("current_inventory_ton", 0.0))
    now_cost = float(decision_row.get(cfg.current_landed_cost_col, 0.0))
    candidate_qty_ton = float(candidate_row["candidate_qty_ton"])

    forced_buy_premium_pct = float(decision_row.get("b_forced_buy_cost_vs_now_pct", 0.08))
    # 먼저 max(0.05, 0.08) = 0.08 그다음 0.08 * 1.5 = 0.12
      # 즉 이 시나리오에서 긴급매입은 기준 단가 대비 12% 프리미엄으로 계산된다.
    forced_buy_premium_pct = max(forced_buy_premium_pct, 0.08) # 최소값을 0.08로 깔고
    forced_buy_premium_pct *= cfg.scenario_library[scenario_name]["emergency_premium_mult"]
    
   # 블록 3) 월별 시뮬레이션 루프: 입고 / 가용재고 / 부족 / 종료재고
    # “그 달에 기존 발주가 얼마나 들어오고, 후보 물량이 들어오고, 수요를 빼고 나면 얼마가 부족한지, 얼마가 남는지 계산한다.
      # 그리고 부족은 ‘긴급매입으로 메웠다’고 본다.”
    rows: List[Dict] = []

    for _, month in flow.iterrows():
        month_idx = int(month["month_idx"])
        open_po_ton = float(month["scenario_open_po_ton"])
        candidate_receipt_ton = candidate_qty_ton if (scenario_arrival_month_idx == month_idx and scenario_arrival_month_idx <= cfg.horizon_months) else 0.0
        available_before_demand = begin_inv + open_po_ton + candidate_receipt_ton
        demand_ton = float(month["scenario_demand_ton"])

        shortage_ton = max(0.0, demand_ton - available_before_demand)
        ending_inventory_ton = max(0.0, available_before_demand - demand_ton)
        emergency_buy_ton = shortage_ton
        
    # 블록 4) 월별 비용 계산
      # “후보 물량은 지금 사는 거니까 현재 단가로 계산하고, 부족해서 나중에 급하게 사는 건 그때 시나리오 가격에 프리미엄까지 붙여 계산한다.”
        open_po_cost = open_po_ton * float(month["scenario_unit_cost_per_ton"]) #기존 발주 입고 비용
        candidate_cost = candidate_receipt_ton * now_cost # 지금 결정한 후보물량 비용
        # 부족발생시 긴급매입 비용 
        emergency_buy_cost = emergency_buy_ton * float(month["scenario_unit_cost_per_ton"]) * (1.0 + forced_buy_premium_pct)
    # 블록 5) 월별 결과 row 적재
      # “각 후보를 각 시나리오에 넣었을 때, 월별로 재고가 어떻게 흐르고 얼마가 부족하고 얼마가 드는지를 행으로 쌓는다.”
        rows.append({
            "decision_id": decision_id,
            "candidate_name": candidate_row["candidate_name"],
            "candidate_qty_ton": candidate_qty_ton,
            "candidate_status": candidate_row["candidate_status"],
            "scenario_name": scenario_name,
            "month_idx": month_idx,
            "begin_inventory_ton": begin_inv,
            "receipt_open_po_ton": open_po_ton,
            "receipt_candidate_ton": candidate_receipt_ton,
            "available_before_demand_ton": available_before_demand,
            "demand_ton": demand_ton,
            "shortage_ton": shortage_ton,
            "ending_inventory_ton": ending_inventory_ton,
            "emergency_buy_ton": emergency_buy_ton,
            "open_po_cost": open_po_cost,
            "candidate_cost": candidate_cost,
            "emergency_buy_cost": emergency_buy_cost,
            "total_month_cost": open_po_cost + candidate_cost + emergency_buy_cost,
        })
        begin_inv = ending_inventory_ton
    # 블록 6) simulate_candidate_under_scenario 반환(“이 후보를 이 시나리오에 넣었을 때의 월별 결과표 1장을 돌려준다.”)
    return pd.DataFrame(rows)

# 블록 7) run_candidate_simulations 시작부
  # “게이트까지 붙은 전체 후보표를 받아, 후보별로 시나리오를 다 돌리겠다.”
def run_candidate_simulations(
    decision_master_df: pd.DataFrame,
    baseline_flow_df: pd.DataFrame,
    gated_candidate_df: pd.DataFrame,
    cfg: PipelineConfig,
) -> pd.DataFrame:
    decision_map = decision_master_df.set_index("decision_id")
    frames: List[pd.DataFrame] = []
    
  # 블록 8) 후보별로 decision row / flow subset 매칭
    # 후보 행 1개를 잡아서, 어떤 decision에서 나온 후보인지 찾고 그 decision의 원래 상태 row를 불러오고 
     # 그 decision의 baseline flow만 잘라온다
       # decision이란? 
     #    항목	        값
     #    자재       	원당
     # 판단 기준시점	2026-04
     #  현재 재고	    12,000톤
     # horizon	       6개월
     # 앞으로 baseline demand/open PO 흐름	이미 계산돼 있음
     # “이 후보가 어느 자재/어느 decision 장면에서 나온 건지 원래 상태를 다시 붙인다.”
    for _, candidate_row in gated_candidate_df.iterrows():
        decision_id = candidate_row["decision_id"]
        decision_row = decision_map.loc[decision_id]
        flow_subset = baseline_flow_df[baseline_flow_df["decision_id"] == decision_id].copy()

# 블록 9) 시나리오 전부 순회하며 시뮬레이션
  # “한 후보안이 base에서는 괜찮아도 stress / shock에서는 다를 수 있으니 시나리오를 다 돌려서 본다.”
        for scenario_name in cfg.scenario_library.keys():
            sim_df = simulate_candidate_under_scenario(
                decision_id=decision_id,
                decision_row=decision_row,
                flow_subset=flow_subset,
                candidate_row=candidate_row,
                scenario_name=scenario_name,
                cfg=cfg,
            )
            frames.append(sim_df)
 # 블록 10) 전체 결과 합치기
  # “이제 후보별·시나리오별 월간 결과를 한 표로 합쳐서, 다음 단계에서 어떤 후보가 더 나은지 비교할 수 있게 만든다.”
    return pd.concat(frames, ignore_index=True)  
