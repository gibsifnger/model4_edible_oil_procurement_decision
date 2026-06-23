
#“같은 구매안이라도 평시, 수요급증, 원가상승, 입고지연 환경에서 결과가 달라지니까 그 환경을 먼저 바꿔 끼우자.”
from __future__ import annotations
from typing import Tuple
import pandas as pd
from ..config import PipelineConfig

# “예정입고가 1개월 늦어지면 원래 2개월차 물량이 3개월차로 밀리고, horizon 밖으로 나가면 이번 판단범위에서는 못 받는 걸로 본다.”
def shift_month_quantity(flow_df: pd.DataFrame, qty_col: str, delay_months: int, horizon_months: int) -> pd.Series:
    shifted = {month_idx: 0.0 for month_idx in range(1, horizon_months + 1)}
    for _, row in flow_df.iterrows():
        new_month = int(row["month_idx"]) + int(delay_months)
        if 1 <= new_month <= horizon_months:
            shifted[new_month] += float(row[qty_col])
    return pd.Series([shifted[int(m)] for m in flow_df["month_idx"]], index=flow_df.index)

# “같은 발주안이라도 수요폭증, 원가상승, 입고지연 환경에서 다시 봐야 한다.”
def apply_scenario(flow_subset: pd.DataFrame, candidate_row: pd.Series, scenario_name: str, cfg: PipelineConfig) -> Tuple[pd.DataFrame, int]:
    scenario = cfg.scenario_library[scenario_name]
    flow = flow_subset.sort_values("month_idx").copy()
    flow["scenario_name"] = scenario_name
    #“평시 대비 수요가 더 터질 수도 있으니, 사용량을 배수로 흔들어 보자.”
    flow["scenario_demand_ton"] = flow["demand_ton"] * scenario["demand_mult"]
    #“같은 물량을 사더라도 원가 환경이 악화되면 체감이 달라지니, 톤당 원가를 배수로 흔들어 보자.”
    flow["scenario_unit_cost_per_ton"] = flow["expected_unit_cost_per_ton"] * scenario["cost_mult"]
    # “평소엔 2개월차에 들어오던 배가 stress에선 3개월차로 밀릴 수 있다.”
    flow["scenario_open_po_ton"] = shift_month_quantity(
        flow_df=flow[["month_idx", "open_po_ton"]],
        qty_col="open_po_ton",
        delay_months=int(scenario["open_po_delay_months"]),
        horizon_months=cfg.horizon_months,
    ).values
    # “이번에 새로 사는 안도, 시나리오가 나빠지면 예상보다 늦게 들어온다고 보자.”
    candidate_arrival_month_idx = int(candidate_row["candidate_arrival_month_idx"])
    if candidate_arrival_month_idx > 0:
        candidate_arrival_month_idx += int(scenario["candidate_delay_months"])

    return flow, candidate_arrival_month_idx
