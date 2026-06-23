from __future__ import annotations

import pandas as pd

from .A_state import build_current_purchase_state
from .B_interpret import interpret_risk_signals
from .C_policy_action import recommend_purchase_actions
from .config import DEFAULT_CONFIG, DATA_PATH, OUTPUT_PATH, ProcurementConfig


SUMMARY_COLUMNS = [
    "as_of_month",
    "item_name",
    "origin_country",
    "current_inventory_ton",
    "monthly_usage_forecast_ton",
    "open_po_ton",
    "lead_time_month",
    "inventory_cover_month",
    "lead_time_gap_month",
    "shortage_expected",
    "moq_check",
    "price_risk_score",
    "fx_freight_risk_score",
    "supply_risk_score",
    "shortage_risk_score",
    "total_risk_score",
    "risk_level",
    "recommended_action",
    "recommended_order_ton",
    "reason_text",
]


def run_pipeline(
    input_path=DATA_PATH,
    output_path=OUTPUT_PATH,
    cfg: ProcurementConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    market_df = pd.read_csv(input_path)
    state_df = build_current_purchase_state(market_df, cfg)
    interpreted_df = interpret_risk_signals(state_df, cfg)
    action_df = recommend_purchase_actions(interpreted_df, cfg)

    output_df = action_df[SUMMARY_COLUMNS].sort_values(
        ["total_risk_score", "item_name"],
        ascending=[False, True],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_df
