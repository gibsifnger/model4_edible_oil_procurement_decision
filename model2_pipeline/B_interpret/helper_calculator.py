"""
no-buy world를 한 번 가정해서 위험 신호 / 필요수량 / 규칙 신호를 뽑아낸다. 
즉, 이 파일은 예측기나 후보생성기가 아니라 “안 샀을 때 어떤 문제가 생기는가”를 해석하는 계산기
"""
from __future__ import annotations
from typing import Dict, List
import numpy as np
import pandas as pd
from ..config import PipelineConfig

# “그 월에 도착한다고 치면 최소 몇 톤이 필요하나?”
def _calc_required_buy_qty_from_arrival(
    starting_inventory_ton: float,
    usage_path: List[float],
    open_po_path: List[float],
    arrival_month_idx: int,
    safety_stock_ton: float,
) -> Dict[str, float]:
    begin_inv = float(starting_inventory_ton) #월별 흐름을 따라가며 깎여 나갈 현재 재고
    begin_inv_at_arrival = float(starting_inventory_ton) #나중에 “도착월 시작재고”를 담아둘 변수
# 새로 살 물량이 도착하기 전까지는 기존 재고랑 기존 PO만으로 버틴다고 보고, 그때까지 남아 있을 재고를 먼저 계산한다
    for month_idx in range(1, arrival_month_idx):
        available = begin_inv + float(open_po_path[month_idx - 1])
        ending_inv = max(0.0, available - float(usage_path[month_idx - 1]))
        begin_inv = ending_inv
    begin_inv_at_arrival = begin_inv
 #누적 필요량 계산용 버퍼 초기화
    cum_receipts = 0.0
    cum_demand = 0.0
    required_qty = 0.0
    max_gap_qty = 0.0
 # “그 달에 도착한다고 해도, 이후 몇 달 동안 전체적으로 얼마나 비는지 보고 그중 가장 큰 부족 구간을 기준으로 필요량을 잡는다.”
    for month_idx in range(arrival_month_idx, len(usage_path) + 1):
        cum_receipts += float(open_po_path[month_idx - 1])
        cum_demand += float(usage_path[month_idx - 1])
        gap_without_candidate = cum_demand - (begin_inv_at_arrival + cum_receipts)
        max_gap_qty = max(max_gap_qty, gap_without_candidate)
        required_qty = max(required_qty, gap_without_candidate + safety_stock_ton)
# 첫 shortage 월만 당장 막는 최소량을 계산
    required_first_shortage_qty = max(
        0.0,
        float(usage_path[arrival_month_idx - 1]) - (begin_inv_at_arrival + float(open_po_path[arrival_month_idx - 1])) + safety_stock_ton,
    )
# begin_inv_at_arrival_ton	             도착월 시작재고
# max_cum_gap_arrival_ton	             최대 누적 부족량
# required_buy_qty_arrival_ton	         안전재고 포함 총 필요수량
# required_buy_qty_first_shortage_ton	 첫 shortage 대응 최소수량
    return {
        "begin_inv_at_arrival_ton": float(begin_inv_at_arrival),
        "max_cum_gap_arrival_ton": float(max(0.0, max_gap_qty)),
        "required_buy_qty_arrival_ton": float(max(0.0, required_qty)),
        "required_buy_qty_first_shortage_ton": float(max(0.0, required_first_shortage_qty)),
    }

# “안 사면 shortage/cover/cost risk가 어떻게 전개되나?”

#이 함수는 새로 아무것도 안 사는 세계(no-buy world) 를 끝까지 흘려보고,
  # 재고위험 A
  # 비용압박 B
  # 필요수량
  # 규칙 flag 를 한꺼번에 만든다.
def _simulate_no_buy_helpers(
    starting_inventory_ton: float,
    usage_path: List[float],
    open_po_path: List[float],
    expected_cost_path: List[float],
    now_cost: float,
    freight_current: float,
    usd_current: float,
    cfg: PipelineConfig,
    
) -> Dict[str, float]: 
    begin_inv = float(starting_inventory_ton)
    
    end_inv_raw_path: List[float] = [] # 음수까지 포함한 실제 말재고
    end_inv_clipped_path: List[float] = [] # 0 아래는 자른 말재고
    shortage_path: List[float] = [] # shortage만 따로 모은 경로

    # no-buy 월별 재고흐름 시뮬레이션(새 발주를 하나도 안 한다고 치고, 기존 재고와 기존 PO만으로 몇 달 버틸 수 있는지 본다.)
    for demand_ton, open_po_ton in zip(usage_path, open_po_path):
        available = begin_inv + float(open_po_ton)
        raw_end_inv = available - float(demand_ton)
        shortage = max(0.0, -raw_end_inv)
        ending_inv = max(0.0, raw_end_inv)

        end_inv_raw_path.append(float(raw_end_inv))
        end_inv_clipped_path.append(float(ending_inv))
        shortage_path.append(float(shortage))
        begin_inv = ending_inv
    # 남은 재고가 그 월 사용량 기준으로 몇 개월치냐
    cover_months = [
        (end_inv / usage if usage > 0 else np.nan)
        for end_inv, usage in zip(end_inv_clipped_path, usage_path)
    ]

    # 첫 shortage 월과 arrival anchor 잡기
    shortage_months = [i + 1 for i, qty in enumerate(shortage_path) if qty > 0]
    a_first_shortage_month_idx = float(shortage_months[0]) if shortage_months else np.nan
    arrival_month_idx = int(a_first_shortage_month_idx) if shortage_months else cfg.lt_months
    arrival_month_idx = max(1, min(arrival_month_idx, cfg.horizon_months))
    # 필요수량 계산 함수 호출
    req = _calc_required_buy_qty_from_arrival(
        starting_inventory_ton=starting_inventory_ton,
        usage_path=usage_path,
        open_po_path=open_po_path,
        arrival_month_idx=arrival_month_idx,
        safety_stock_ton=cfg.safety_stock_ton,
    )
    # A 축 재고위험 계산 “안 사면 재고가 얼마나 무너지는지, shortage가 실제로 터지는지 보는 축이다.”
    a_min_end_inv_ton = float(np.min(end_inv_raw_path))
    a_min_cover_months = float(np.nanmin(cover_months))
    a_emergency_buy_needed_flag = int(any(qty > 0 for qty in shortage_path))
    baseline_total_shortage_ton = float(np.sum(shortage_path))
    
    # B 축 비용압박 계산(현재 원가 대비 미래월 원가가 얼마나 비싸지는지 계산한다.)
    cost_vs_now = [
        (float(cost) / float(now_cost) - 1.0) if now_cost > 0 else 0.0
        for cost in expected_cost_path
    ]
    peak_cost_vs_now_pct = float(np.max(cost_vs_now))
    high_cost_month_count = int(sum(1 for v in cost_vs_now if v >= 0.05))
    # forced buy cost 계산
    if shortage_months:
        shortage_month_costs = [cost_vs_now[idx - 1] for idx in shortage_months]
        forced_buy_cost_vs_now_pct = float(np.max(shortage_month_costs))
    else:
        forced_buy_cost_vs_now_pct = float(max(0.0, peak_cost_vs_now_pct))

    b_forced_buy_flag = int(a_emergency_buy_needed_flag == 1)
    
    # premium score 계산(“나중에 웃돈 주고 급하게 살 위험이 얼마나 심한가를 종합점수로 만든다.”)
    freight_stress = np.clip((freight_current - 95.0) / 30.0, 0.0, 1.0)
    fx_stress = np.clip((usd_current - 1260.0) / 80.0, 0.0, 1.0)
    shortage_severity = np.clip(max(shortage_path) / cfg.monthly_usage_base_ton, 0.0, 1.0)
    premium_score = 100.0 * np.clip(
        0.45 * max(0.0, peak_cost_vs_now_pct / 0.10)
        + 0.25 * freight_stress
        + 0.15 * fx_stress
        + 0.15 * shortage_severity,
        0.0,
        1.0,
    )
    # 재고위험 규칙이다. shortage 발생 / 최소 말재고 음수 / 최소 커버 0.35 미만 /
      # 커버 0.50 미만 + shortage 2개월 이내 중 하나면 1이다.
    target_a = int(
        (a_emergency_buy_needed_flag == 1)
        or (a_min_end_inv_ton < 0)
        or (a_min_cover_months < 0.35)
        or (
            (a_min_cover_months < 0.50)
            and (not pd.isna(a_first_shortage_month_idx))
            and (a_first_shortage_month_idx <= 2)
        )
    )
    # 비용압박 규칙이다. shortage 강제매입 + 5% 이상 비쌈 / premium score 60 이상
    #  peak cost 8% 이상 / 5% 이상 비싼 달이 2개월 이상 중 하나면 1이다.
    target_b = int(
        ((b_forced_buy_flag == 1) and (forced_buy_cost_vs_now_pct >= 0.05))
        or (premium_score >= 60.0)
        or (peak_cost_vs_now_pct >= 0.08)
        or ((peak_cost_vs_now_pct >= 0.05) and (high_cost_month_count >= 2))
    )

    return {
        "a_min_end_inv_ton": float(a_min_end_inv_ton),
        "a_min_cover_months": float(a_min_cover_months),
        "a_emergency_buy_needed_flag": int(a_emergency_buy_needed_flag),
        "a_first_shortage_month_idx": a_first_shortage_month_idx,
        "baseline_total_shortage_ton": baseline_total_shortage_ton,
        **req,
        "b_peak_cost_vs_now_pct": float(peak_cost_vs_now_pct),
        "b_forced_buy_flag": int(b_forced_buy_flag),
        "b_forced_buy_cost_vs_now_pct": float(forced_buy_cost_vs_now_pct),
        "b_emergency_premium_score": float(premium_score),
        "b_high_cost_month_count": int(high_cost_month_count),
        "target_a_rule": int(target_a),
        "target_b_rule": int(target_b),
    }


__all__ = [
    "_calc_required_buy_qty_from_arrival",
    "_simulate_no_buy_helpers",
]
