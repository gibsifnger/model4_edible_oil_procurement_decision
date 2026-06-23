# 이 파일은 simulator.py가 만든 월별 long table을 그대로 들고 가지 않고,
   # 후보 비교가 가능하도록 요약표 2장으로 바꾸는 파일이다.
from __future__ import annotations
import pandas as pd

# 블록 1) 함수 선언
# “후보별 월별 결과가 너무 길기 때문에, 의사결정에 필요한 비교표로 압축하겠다.”
def build_scenario_compare_summary(simulation_result_df: pd.DataFrame):
    # 블록 2) scenario_summary_df 생성: 후보×시나리오 단위 요약
     # “각 후보를 각 시나리오에 넣었을 때, 월별 결과를 길게 보지 말고
      # 총 부족량, shortage 발생 여부, 최저 재고, 총비용, 긴급매입량, 마지막 재고로 압축해 보자.”
    scenario_summary_df = (
        simulation_result_df.groupby(
            ["decision_id", "candidate_name", "candidate_qty_ton", "candidate_status", "scenario_name"],
            as_index=False,
        )
        .agg(
            total_shortage_ton=("shortage_ton", "sum"),
            any_shortage_flag=("shortage_ton", lambda s: int((s > 0).any())),
            min_ending_inventory_ton=("ending_inventory_ton", "min"),
            total_cost=("total_month_cost", "sum"),
            total_emergency_buy_ton=("emergency_buy_ton", "sum"),
            last_ending_inventory_ton=("ending_inventory_ton", "last"),
        )
    )
 # 블록 3) observe_ref 생성: 관망안 비용 기준표 만들기(“비용 비교의 기준점을 ‘안 사는 안(observe)’으로 잡겠다.”)
    observe_ref = (
        scenario_summary_df[scenario_summary_df["candidate_name"] == "observe"]
        [["decision_id", "scenario_name", "total_cost"]]
        .rename(columns={"total_cost": "observe_total_cost"})
    )
# 블록 4) observe 비용 붙이기 + 관망안 대비 비용비율 계산
 # “이 후보를 선택했을 때, 그 시나리오에서 아예 안 사는 안과 비교해 비용이 몇 % 더 드는지/덜 드는지를 보자.”
    scenario_summary_df = scenario_summary_df.merge(
        observe_ref,
        on=["decision_id", "scenario_name"],
        how="left",
    )

    scenario_summary_df["cost_vs_observe_pct"] = (
        (scenario_summary_df["total_cost"] - scenario_summary_df["observe_total_cost"])
        / scenario_summary_df["observe_total_cost"]
    )

# 블록 5) robust_summary_df 생성: 시나리오 전체를 묶은 강건성 요약
 # “이 후보가 base에서는 괜찮고 shock에서는 망가질 수도 있으니, 전 시나리오를 다 묶어서 ‘진짜 버티는 후보냐’를 보자.”
    robust_summary_df = (
        scenario_summary_df.groupby(
            ["decision_id", "candidate_name", "candidate_qty_ton", "candidate_status"],
            as_index=False,
        )
        .agg(
            scenario_count=("scenario_name", "nunique"),
            robust_no_shortage_all_scenarios=("any_shortage_flag", lambda s: int((s == 0).all())),
            worst_case_shortage_ton=("total_shortage_ton", "max"),
            worst_case_cost_vs_observe_pct=("cost_vs_observe_pct", "max"),
            worst_case_min_ending_inventory_ton=("min_ending_inventory_ton", "min"),
            avg_total_cost=("total_cost", "mean"),
        )
    )

# “한 장은 시나리오별 비교표, 한 장은 시나리오 전체를 묶은 강건성 비교표로 넘긴다.”
    return scenario_summary_df, robust_summary_df