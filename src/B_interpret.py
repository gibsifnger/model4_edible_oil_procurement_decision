from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ProcurementConfig


def _clip_score(value: pd.Series) -> pd.Series:
    return value.clip(lower=0, upper=100).round(1)


def interpret_risk_signals(
    state_df: pd.DataFrame,
    cfg: ProcurementConfig,
) -> pd.DataFrame:
    """Convert procurement state and market inputs into explainable risk scores."""
    out = state_df.copy()

    price_momentum = out["price_change_3m_pct"].fillna(0)
    price_volatility = out["price_volatility_score"].fillna(0)
    fx_change = out["fx_change_1m_pct"].fillna(0)
    freight_change = out["freight_change_1m_pct"].fillna(0)
    supplier_reliability_gap = 100 - out["supplier_reliability_score"].fillna(75)
    origin_disruption = out["origin_disruption_score"].fillna(0)
    customs_delay = out["customs_delay_risk_score"].fillna(0)

    out["price_risk_score"] = _clip_score((price_momentum * 4.0) + (price_volatility * 0.65) + 20)
    out["fx_freight_risk_score"] = _clip_score((fx_change * 5.0) + (freight_change * 4.0) + 25)
    out["supply_risk_score"] = _clip_score(
        supplier_reliability_gap * 0.45 + origin_disruption * 0.35 + customs_delay * 0.20
    )

    shortage_base = np.where(out["shortage_expected"], 65, 20)
    cover_penalty = (cfg.target_cover_month - out["inventory_cover_month"]).clip(lower=0) * 16
    lead_time_penalty = out["lead_time_gap_month"].clip(lower=0) * 12
    out["shortage_risk_score"] = _clip_score(shortage_base + cover_penalty + lead_time_penalty)

    out["total_risk_score"] = _clip_score(
        out["price_risk_score"] * 0.25
        + out["fx_freight_risk_score"] * 0.20
        + out["supply_risk_score"] * 0.25
        + out["shortage_risk_score"] * 0.30
    )

    out["risk_level"] = np.select(
        [
            out["total_risk_score"] >= cfg.high_risk_threshold,
            out["total_risk_score"] >= cfg.medium_risk_threshold,
        ],
        ["HIGH", "MEDIUM"],
        default="LOW",
    )

    return out
