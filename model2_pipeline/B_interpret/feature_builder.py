from __future__ import annotations
import pandas as pd
from ..config import PipelineConfig

"""HGB용 feature frame 생성.
    - raw/helper/wide 컬럼 중 scorer에 바로 넣을 숫자 feature만 추린다.
    - train / inference에서 같은 feature 집합을 쓰도록 고정한다.
"""
# decision_master_df에서 모델이 바로 먹을 숫자 입력표(X) 를 만든다

#상태표에 정보가 많아도, 모델에 아무거나 넣으면 안 된다.
 # 회사 장면을 설명하는 숫자들만 골라서, 항상 같은 양식으로 모델에 넣자는 단계다.”
def build_model_feature_frame(decision_master_df: pd.DataFrame, cfg: PipelineConfig) -> pd.DataFrame:
    df = decision_master_df.copy()
    # “앞으로 몇 달치 수요를 볼 때, 지금 잡혀 있는 발주만으로 얼마나 버틸 수 있는지 숫자로 만든다.”
    # 앞으로 horizon 동안 총 얼마나 쓸 예정인가
    df["usage_horizon_total_ton"] = sum(df[f"usage_m{i}_ton"] for i in range(1, cfg.horizon_months + 1))
    # 이미 발주되어 들어올 예정 물량이 총 얼마인가
    df["open_po_horizon_total_ton"] = sum(df[f"open_po_m{i}_ton"] for i in range(1, cfg.horizon_months + 1))
    # 예정 수요를 기존 오픈 PO가 얼마나 덮고 있는가
    df["open_po_coverage_ratio"] = df["open_po_horizon_total_ton"] / df["usage_horizon_total_ton"].clip(lower=1.0)
  
    # 비용 경로 요약 feature 생성
       # 앞으로 horizon 동안의 예상 landed cost 경로를 요약해서, 앞으로 가장 비싼 순간이 지금보다 얼마나 높은지, 그리고
         # 앞으로 평균적으로 지금보다 얼마나 높은지(%) 를 만든다.
         
    # 앞으로 가장 비싼 달이 지금보다 얼마나 비싼가
    df["cost_path_peak_minus_now"] = df[[f"expected_landed_cost_m{i}_per_ton" for i in range(1, cfg.horizon_months + 1)]].max(axis=1) - df[cfg.current_landed_cost_col]
    # 향후 평균 원가가 지금 대비 몇 % 높은가/낮은가
    df["cost_path_mean_vs_now_pct"] = (
        df[[f"expected_landed_cost_m{i}_per_ton" for i in range(1, cfg.horizon_months + 1)]].mean(axis=1) / df[cfg.current_landed_cost_col].clip(lower=1.0)
    ) - 1.0
    
    # “모델에게 ‘가격만 보지 말고’, 재고, 부족월, 강제구매 여부, 비용 경로, 자금압박, 이미 잡힌 발주까지 같이 보게 만든다.”
    
    
    # 모델에 넣을 feature 목록 고정
    # 묶음	                 실제 컬럼	   의미
   # 현재 상태/시장	current_inventory_ton, current_landed_cost, global_raw_sugar_price, usdkrw, freight_index	지금 재고와 현재 원가, 외부 시장 수준
   # 단기/중기 변화율	sugar_ret_1m, usdkrw_ret_1m, freight_ret_1m, sugar_ret_3m, usdkrw_ret_3m, freight_ret_3m	최근 1개월/3개월 추세
   # 자금 압박	working_capital_pressure_score	지금 자금 부담
   # A 상태 생성 결과	a_min_end_inv_ton, a_min_cover_months, a_emergency_buy_needed_flag, a_first_shortage_month_idx	운영상태가 얼마나 빡빡한가
   # B 해석 결과	b_peak_cost_vs_now_pct, b_forced_buy_flag, b_forced_buy_cost_vs_now_pct, b_emergency_premium_score, b_high_cost_month_count	왜 위험/기회인지에 대한 해석값
   # 부족/갭 관련	baseline_total_shortage_ton, max_cum_gap_arrival_ton, required_buy_qty_arrival_ton, required_buy_qty_first_shortage_ton	실제로 얼마나 모자라는가
   # horizon 집계	usage_horizon_total_ton, open_po_horizon_total_ton, open_po_coverage_ratio, cost_path_peak_minus_now, cost_path_mean_vs_now_pct	여러 월 정보를 압축한 집계값
    feature_cols = [
        "current_inventory_ton",
        cfg.current_landed_cost_col,
        "global_raw_sugar_price",
        "usdkrw",
        "freight_index",
        "sugar_ret_1m",
        "usdkrw_ret_1m",
        "freight_ret_1m",
        "sugar_ret_3m",
        "usdkrw_ret_3m",
        "freight_ret_3m",
        "working_capital_pressure_score",
        "a_min_end_inv_ton",
        "a_min_cover_months",
        "a_emergency_buy_needed_flag",
        "a_first_shortage_month_idx",
        "b_peak_cost_vs_now_pct",
        "b_forced_buy_flag",
        "b_forced_buy_cost_vs_now_pct",
        "b_emergency_premium_score",
        "b_high_cost_month_count",
        "baseline_total_shortage_ton",
        "max_cum_gap_arrival_ton",
        "required_buy_qty_arrival_ton",
        "required_buy_qty_first_shortage_ton",
        "usage_horizon_total_ton",
        "open_po_horizon_total_ton",
        "open_po_coverage_ratio",
        "cost_path_peak_minus_now",
        "cost_path_mean_vs_now_pct",
    ]
# “이제 상태표를 모델용 숫자표로 바꿨다.
# 비어 있는 값은 일단 0으로 정리해서 모델이 멈추지 않게 만든다.”
    X = df[feature_cols].copy().fillna(0.0)
    return X
