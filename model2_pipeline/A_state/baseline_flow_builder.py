'''
state_builder.py가 “판단 시작 row 자체를 만드는 파일”이었다면,
baseline_flow_builder.py는 그 row를 “미래월 기준 long 흐름표”로 바꾸는 파일이다. 
즉, 상태를 새로 만드는 파일이 아니라 상태를 시계열 축으로 펼치는 연결층이다.
'''
from __future__ import annotations
from typing import Dict, List
import pandas as pd
from ..config import PipelineConfig #horizon, default cost, material code 같은 기준값 참조

#“월별 사용량/입고/예상원가 값이 비어 있으면 일단 기본값으로 계산을 이어간다.”
def _get_value(row: pd.Series, key: str, default: float) -> float:
    value = row.get(key, default)
    if pd.isna(value):
        return float(default)
    return float(value)

# decision row를 horizon long flow로 펼친다.
  # row unit은 decision master에서는 `원재료-월-의사결정시점`
  # baseline flow에서는 `원재료-월-의사결정시점-미래월`
  # helper와 simulation이 같은 미래월 축을 보도록 만드는 연결층이다.
def build_baseline_flow_df(decision_master_df: pd.DataFrame, cfg: PipelineConfig) -> pd.DataFrame:
    rows: List[Dict] = []
    for _, row in decision_master_df.iterrows():
        # “이번 달 판단 한 건에 대해 앞으로 1개월차, 2개월차, 3개월차… 흐름을 따로 행으로 펼치자.”
        for month_idx in range(1, cfg.horizon_months + 1):
            rows.append({
                "decision_id": row["decision_id"],
                "decision_month": row["decision_month"],
                "material_code": row.get("material_code", cfg.material_code),
                "month_idx": month_idx,
                # “월별 사용량 path가 이미 있으면 그걸 쓰고, 없으면 기본 월사용량으로 메운다.”
                "demand_ton": _get_value(row, f"usage_m{month_idx}_ton", cfg.monthly_usage_base_ton),
                # “그 달 들어올 기존 PO가 없으면 그냥 0톤이다.”
                "open_po_ton": _get_value(row, f"open_po_m{month_idx}_ton", 0.0),
                # “미래월 예상 landed cost가 없으면 일단 현재 원가를 기준값으로 쓴다.”
                "expected_unit_cost_per_ton": _get_value(
                    row,
                    f"expected_landed_cost_m{month_idx}_per_ton",
                    _get_value(row, cfg.current_landed_cost_col, 0.0),
                ),
            })

    return pd.DataFrame(rows).sort_values(["decision_id", "month_idx"]).reset_index(drop=True)
