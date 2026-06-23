
# 현재 row를 기준으로 구매 후보안(candidate set)을 생성
from __future__ import annotations
from typing import Dict, List
import numpy as np
import pandas as pd
from ..config import PipelineConfig

# 블록 1) lot 단위 반올림 + 후보 수량 정규화
 # “필요하다고 계산된 수량이 6,200톤이어도, 실제 발주는 MOQ와 lot 규칙을 맞춰서 내야 한다.”
def round_up_to_lot(qty_ton: float, lot_multiple_ton: float) -> float:
    if qty_ton <= 0:
        return 0.0
    return float(np.ceil(qty_ton / lot_multiple_ton) * lot_multiple_ton)


def normalize_nonzero_candidate(qty_ton: float, moq_ton: float, lot_multiple_ton: float) -> float:
    if qty_ton <= 0:
        return 0.0
    return round_up_to_lot(max(qty_ton, moq_ton), lot_multiple_ton)

# 블록 2) derive_shortage_anchored_qty: 부족 규모를 기준으로 대표 필요후보 만들기
"""부족 규모에 비례한 required candidate를 만든다.
    수정 원칙
    ---------
    - baseline helper가 계산한 `required_buy_qty_arrival_ton`을 1순위로 쓴다.
    - 없으면 `max_cum_gap_arrival_ton + safety_stock`을 fallback으로 쓴다.
    - 이것도 없으면 총 shortage와 first-shortage relief를 보조적으로 쓴다.
    - 여기서 일부러 capacity를 미리 자르지 않는다.
      이유: "필요수량"과 "실행가능수량"은 다른 층이다. 실행가능성은 gate가 자른다.
"""
# 이 함수는 이 값들을 candidates에 넣고, 마지막에 max([0.0, *candidates])로 가장 큰 부족기준값을 대표값으로 잡는다.
  # 1~4개중에 가장 큰 값을 잡는거임. 
 # “이 row가 가진 부족 문제를 볼 때, 어느 정도 물량을 사야 의미 있는 해소 후보가 되는지 대표 수량을 하나 만든다.”
   # 1순위 : 도착시점 기준으로 필요한 구매수량
   # 2순위 : arrival 기준 최대 누적 갭
   # 3순위 : baseline 전체 shortage 규모
   # 4순위 : 첫 shortage 해소 중심 필요수량
def derive_shortage_anchored_qty(row: pd.Series, cfg: PipelineConfig) -> float:

    required_qty = float(row.get("required_buy_qty_arrival_ton", np.nan))
    max_gap_qty = float(row.get("max_cum_gap_arrival_ton", np.nan))
    total_shortage = float(row.get("baseline_total_shortage_ton", np.nan))
    first_shortage_relief = float(row.get("required_buy_qty_first_shortage_ton", np.nan))

    candidates = []
    if pd.notna(required_qty):
        candidates.append(required_qty)
    if pd.notna(max_gap_qty):
        candidates.append(max_gap_qty + cfg.safety_stock_ton)
    if pd.notna(first_shortage_relief):
        candidates.append(first_shortage_relief)
    if pd.notna(total_shortage):
        candidates.append(total_shortage * 0.85)

    raw_qty = max([0.0, *candidates])
    return normalize_nonzero_candidate(raw_qty, cfg.moq_ton, cfg.lot_multiple_ton)

# 블록 3) build_ladder_candidates: MOQ와 shortage_anchored 사이의 중간 후보 만들기
 # “최소발주 1안하고, 큰 필요수량 1안만 두면 중간 선택지가 비니까, 실무적으로 가능한 절충안 후보들을 쭉 깔아두는 단계다.”
"""MOQ와 shortage anchored 사이의 중간 후보군을 추가한다.
    예시 (moq=5000, lot=2500, shortage_anchored=55000)
    - MOQ+1lot = 7500
    - MOQ+2lot = 10000
    - ...
    - MOQ+14lot = 40000
    - shortage_anchored = 55000

    목적:
    - 7,500톤과 55,000톤 사이가 비는 문제를 줄인다.
    - 필요한 물량과 실행 가능한 물량 사이의 더 많은 trade-off를 보게 한다.
"""
def build_ladder_candidates(
    shortage_anchored_qty: float,
    moq_ton: float,
    lot_multiple_ton: float,
) -> List[tuple[str, float]]:

    specs: List[tuple[str, float]] = [("MOQ", moq_ton)]

    # 중간 ladder 후보: MOQ+1lot ~ MOQ+14lot
    for n_lot in range(1, 15):
        qty = moq_ton + n_lot * lot_multiple_ton
        if shortage_anchored_qty > 0 and qty >= shortage_anchored_qty:
            break
        specs.append((f"MOQ+{n_lot}lot", qty))

    specs.append(("shortage_anchored", shortage_anchored_qty))
    return specs

# 블록 4) generate_candidate_df 시작부: row별 후보 생성 준비
"""후보안 세트 생성.
    기본 후보
    ---------
    - observe
    - MOQ
    - MOQ+Nlot ladder
    - shortage_anchored (required candidate 성격)
    주의
    ----
    - 최종 목적은 "정답 1개"를 내는 게 아니라, 비교 가능한 후보안을 세우는 것이다.
    - shortage_anchored는 now row의 문제 규모를 실제로 반영해야 한다.
    - 중간 ladder 후보는 실무형 탐색력을 높이기 위해 추가한다.
"""
# “현재 이 자재/이 시점의 주문 규칙(MOQ, lot), 현재 원가, 예상 도착월, 부족 대표수량을 먼저 뽑아온다.”
def generate_candidate_df(decision_master_df: pd.DataFrame, cfg: PipelineConfig) -> pd.DataFrame:

    rows: List[Dict] = []

    for _, row in decision_master_df.iterrows():
        moq_ton = float(row.get("moq_ton", cfg.moq_ton))
        lot_multiple_ton = float(row.get("lot_multiple_ton", cfg.lot_multiple_ton))
        now_cost = float(row.get(cfg.current_landed_cost_col, 0.0))

        arrival_month_idx = int(row.get("candidate_arrival_month_idx", cfg.lt_months))
        arrival_month_idx = max(1, min(arrival_month_idx, cfg.horizon_months))#기본적으로 1~6범위로 잘림

        shortage_anchored_qty = derive_shortage_anchored_qty(row, cfg)

# 블록 5) 후보 사양(candidate_specs) 만들기(“비교 대상은 ‘얼마를 살까’만이 아니라, 안 사는 것도 하나의 정책후보다.”)
        candidate_specs = [("observe", 0.0)]
        candidate_specs.extend(
            build_ladder_candidates(
                shortage_anchored_qty=shortage_anchored_qty,
                moq_ton=moq_ton,
                lot_multiple_ton=lot_multiple_ton,
            )
        )
# 블록 6) 후보 수량 중복 제거 + 최종 후보 행 생성
 # “후보안을 그냥 이름만 만드는 게 아니라, 각 후보마다 실제 발주톤수 / 도착월 / 지금 기준 금액 / 부족근거를 붙여서
   # 나중에 ‘어느 안이 더 낫나’를 비교할 수 있게 만든다.”
        seen_qty = set()
        for candidate_name, qty_ton in candidate_specs:
            normalized_qty = 0.0 if qty_ton == 0 else normalize_nonzero_candidate(qty_ton, moq_ton, lot_multiple_ton)
            if normalized_qty in seen_qty:
                continue
            seen_qty.add(normalized_qty)
# rows.append({...})로 최종 후보 행을 만든다.
            rows.append(
                {
                    "decision_id": row["decision_id"], #원래 decision row 식별자
                    "material_code": row.get("material_code", cfg.material_code),#자재코드
                    "candidate_name": candidate_name, #후보 이름 (observe, MOQ, MOQ+1lot, shortage_anchored 등)
                    "candidate_qty_ton": normalized_qty,#정규화된 후보 물량
                    "candidate_arrival_month_idx": arrival_month_idx if normalized_qty > 0 else 0,#후보 도착월. 다만 0톤이면 0
                    "candidate_unit_cost_per_ton_now": now_cost, #현재 톤당 원가
                    "candidate_po_value_now": normalized_qty * now_cost, #현재 기준 발주금액 = normalized_qty * now_cost
                    "required_buy_qty_arrival_ton": float(row.get("required_buy_qty_arrival_ton", np.nan)), #원 row의 필요수량 근거 보존
                    "max_cum_gap_arrival_ton": float(row.get("max_cum_gap_arrival_ton", np.nan)), # 원 row의 최대 갭 근거 보존
                    "baseline_total_shortage_ton": float(row.get("baseline_total_shortage_ton", np.nan)), #원 row의 shortage 총량 근거 보존
                }
            )
# 지금까지 쌓은 후보 row들을 DataFrame으로 만들어 반환한다.
 # “이제 각 decision row마다 관망안 / 최소발주안 / 중간절충안 / 필요수량안이 행으로 펼쳐진 후보표가 만들어졌다.”
    return pd.DataFrame(rows)