
# 후보안(candidate)에 대해 MOQ / lot / 창고 / 운전자본 / 도착시점 게이트를 적용해
  # feasible / conditional / blocked 상태를 부여
from __future__ import annotations
from typing import Dict, List
import numpy as np
import pandas as pd
from ..config import PipelineConfig

# 블록 1) simulate_inventory_only: 후보안 투입 시 간이 재고 경로 계산
"""gate용 간이 재고 경로.
    주의
    ----
    - ending inventory 뿐 아니라 receipt 직후 inventory도 같이 본다.
    - warehouse gate는 ending만 보면 과대수량을 놓칠 수 있다.
"""
#“이 후보 물량을 이 도착월에 넣으면, 월별로 재고가 어떻게 흘러가는지 간단히 먼저 본다.”
def simulate_inventory_only(
    flow_subset: pd.DataFrame,
    current_inventory_ton: float,
    candidate_qty_ton: float,
    arrival_month_idx: int,
) -> pd.DataFrame:

    detail_rows = []
    begin_inv = float(current_inventory_ton)

# 블록 2) 월별 재고 흐름 계산 본체
 # “지금 재고에서 시작해서, 기존 발주 들어오고 후보 발주 들어오고, 수요를 빼고 나면 월별로 재고와 부족이 어떻게 되는지 본다.”
    for _, row in flow_subset.sort_values("month_idx").iterrows():
        receipt_candidate = candidate_qty_ton if int(row["month_idx"]) == int(arrival_month_idx) else 0.0
        receipt_open_po = float(row["open_po_ton"])
        inventory_after_receipts = begin_inv + receipt_open_po + receipt_candidate
        demand = float(row["demand_ton"])
        shortage = max(0.0, demand - inventory_after_receipts)
        ending_inv = max(0.0, inventory_after_receipts - demand)

        detail_rows.append({
            "month_idx": int(row["month_idx"]),
            "begin_inv_ton": begin_inv,
            "receipt_open_po_ton": receipt_open_po,
            "receipt_candidate_ton": receipt_candidate,
            "inventory_after_receipts_ton": inventory_after_receipts,
            "demand_ton": demand,
            "shortage_ton": shortage,
            "ending_inv_ton": ending_inv,
        })
        begin_inv = ending_inv
# “이 후보를 넣었을 때의 월별 재고흐름 표를 만든다.”
    return pd.DataFrame(detail_rows)

# 블록 4) apply_operating_gate 시작: 후보별 게이트 판정 준비
 # “후보표를 받아서, 각 후보가 실제로 운영상 통과 가능한지 하나씩 심사하겠다.”
def apply_operating_gate(
    decision_master_df: pd.DataFrame,
    baseline_flow_df: pd.DataFrame,
    candidate_df: pd.DataFrame,
    cfg: PipelineConfig,
) -> pd.DataFrame:
    decision_map = decision_master_df.set_index("decision_id")
    results: List[Dict] = []
    
# 블록 5) 후보 1개씩 돌면서 원래 decision row와 baseline flow 연결
 # “이 후보안이 어떤 decision row에서 나온 건지 원본 상태를 다시 불러오고, 그 row 기준 MOQ / lot / 창고cap을 적용하겠다.”
 # 후보 행 하나를 집어서,
 # 그 후보가 속한 decision_id를 찾고 해당 decision 원 row를 꺼내고 그 decision의 baseline flow만 잘라오고
 # 그 후보의 수량/도착월과, 원 row의 MOQ/lot/capacity를 준비한다.
   # 즉 후보행 + 원상태 + baseline flow를 한 묶음으로 맞춘다.
    for _, cand in candidate_df.iterrows():
        decision_id = cand["decision_id"]
        row = decision_map.loc[decision_id]
        flow_subset = baseline_flow_df[baseline_flow_df["decision_id"] == decision_id].copy()

        candidate_qty_ton = float(cand["candidate_qty_ton"])
        arrival_month_idx = int(cand["candidate_arrival_month_idx"])

        moq_ton = float(row.get("moq_ton", cfg.moq_ton))
        lot_multiple_ton = float(row.get("lot_multiple_ton", cfg.lot_multiple_ton))
        warehouse_capacity_ton = float(row.get("warehouse_capacity_ton", cfg.warehouse_capacity_ton))
# 블록 6) 후보 투입 후 재고경로 계산
 # “이 물량을 이 달에 넣으면 실제로 재고가 어떻게 흐르는지 후보별로 먼저 본다.”
        inventory_path = simulate_inventory_only(
            flow_subset=flow_subset,
            current_inventory_ton=float(row.get("current_inventory_ton", 0.0)),
            candidate_qty_ton=candidate_qty_ton,
            arrival_month_idx=arrival_month_idx,
        )
# 블록 7) MOQ / lot gate
 # “안 사는 안은 당연히 통과. 사는 안이면 최소주문량 이상이고 lot 규칙에 맞아야 한다.”
        moq_pass = (candidate_qty_ton == 0.0) or (candidate_qty_ton >= moq_ton)
        lot_pass = (candidate_qty_ton == 0.0) or np.isclose(candidate_qty_ton % lot_multiple_ton, 0.0)

        # ending inventory가 아니라 receipt 직후 재고가 cap을 넘는지 본다.
        # 블록 8) warehouse gate
          # “월말에 재고가 많이 안 남더라도, 입고 순간에 창고를 넘치게 만들면 그 후보는 막아야 한다.”
        warehouse_pass = inventory_path["inventory_after_receipts_ton"].max() <= warehouse_capacity_ton
        
        # 블록 9) working capital gate
          # “운전자본 압박이 너무 높으면 이 후보는 막고, 애매하게 높으면 바로 막진 않지만 주의표시를 단다.”
        wc_pressure = float(row.get("working_capital_pressure_score", 0.0))
        if candidate_qty_ton == 0.0:
            wc_gate = "pass"
        elif wc_pressure >= cfg.wc_pressure_block_threshold:
            wc_gate = "blocked"
        elif wc_pressure >= cfg.wc_pressure_conditional_threshold:
            wc_gate = "conditional"
        else:
            wc_gate = "pass"
            
        # 블록 10) arrival timing gate
          # “이 물량이 정말 부족이 시작되기 전에 들어오냐, 딱 맞춰 들어오냐, 아니면 너무 늦게 들어오냐를 본다.”
        first_shortage_month_idx = row.get("a_first_shortage_month_idx", np.nan)
        if candidate_qty_ton == 0.0:
            arrival_gate = "pass"
        elif pd.isna(first_shortage_month_idx):
            arrival_gate = "pass"
        elif arrival_month_idx > int(first_shortage_month_idx):
            arrival_gate = "blocked"
        elif arrival_month_idx == int(first_shortage_month_idx):
            arrival_gate = "conditional"
        else:
            arrival_gate = "pass"
    
       # 블록 11) hard fail reason 구성
          # “이 후보를 아예 못 쓰게 만드는 치명적 이유들을 모은다.”
        hard_fail_reasons = []
        if not moq_pass:
            hard_fail_reasons.append("MOQ gate fail")
        if not lot_pass:
            hard_fail_reasons.append("lot multiple gate fail")
        if not warehouse_pass:
            hard_fail_reasons.append("warehouse gate fail")
        if arrival_gate == "blocked":
            hard_fail_reasons.append("arrival timing gate fail")
        if wc_gate == "blocked":
            hard_fail_reasons.append("working capital gate fail")
        
        # 블록 12) soft warning 구성(“아예 금지는 아니지만, 운영상 빡빡한 후보라는 표시를 달아둔다.”)
        soft_warnings = []
        if wc_gate == "conditional":
            soft_warnings.append("working capital pressure high")
        if arrival_gate == "conditional":
            soft_warnings.append("arrival timing tight")

       # 블록 13) 최종 candidate status 결정
         # “이 후보는 아예 못 쓰는가, 쓸 수는 있지만 주의가 필요한가, 아니면 무난히 가능한가를 최종 상태로 붙인다.”
        if hard_fail_reasons:
            candidate_status = "blocked"
        elif soft_warnings:
            candidate_status = "conditional"
        else:
            candidate_status = "feasible"
      
        # 블록 14) 결과 행 append
        # “후보별로 단순 pass/fail만 남기는 게 아니라, 왜 막혔는지 / 어느 정도까지는 가능한지 / 넣으면 
        # 재고와 shortage가 어떻게 보이는지까지 같이 실어 보낸다.”
        results.append({
            **cand.to_dict(),
            "moq_gate_pass": int(moq_pass),
            "lot_multiple_gate_pass": int(lot_pass),
            "warehouse_gate_pass": int(warehouse_pass),
            "working_capital_gate_result": wc_gate,
            "arrival_timing_gate_result": arrival_gate,
            "projected_max_inventory_after_receipts_ton_base": float(inventory_path["inventory_after_receipts_ton"].max()),
            "projected_max_end_inv_ton_base": float(inventory_path["ending_inv_ton"].max()),
            "projected_total_shortage_ton_base": float(inventory_path["shortage_ton"].sum()),
            "candidate_status": candidate_status,
            "hard_fail_reason": "; ".join(hard_fail_reasons),
            "soft_warning_reason": "; ".join(soft_warnings),
        })

    # “이제 후보들은 단순 후보가 아니라, 실행가능성 상태가 붙은 후보표가 된다.”
    return pd.DataFrame(results)
