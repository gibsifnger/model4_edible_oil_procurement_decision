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
    cover_penalty = (out["required_cover_month"] - out["inventory_cover_month"]).clip(lower=0) * 18
    lead_time_penalty = out["lead_time_gap_month"].clip(lower=0) * 12
    out["shortage_risk_score"] = _clip_score(shortage_base + cover_penalty + lead_time_penalty)

    out["landed_cost_risk_score"] = _clip_score(
        out["landed_cost_change_pct"].fillna(0) * 5.0
        + out["tariff_rate_pct"].fillna(0) * 2.0
        + out["fx_freight_risk_score"] * 0.25
        + 10
    )

    signal_map = {
        "SUPPLY_STRESS": 24,
        "TIGHT_UPSIDE": 18,
        "CUSTOMS_WATCH": 10,
        "STABLE": 0,
        "SOFTENING": -8,
    }
    market_signal_score = out["market_signal"].map(signal_map).fillna(8)
    low_confidence_penalty = (100 - out["forecast_confidence_score"].fillna(60)).clip(lower=0) * 0.25
    out["forecast_risk_score"] = _clip_score(
        out["forecast_price_change_3m_pct"].fillna(0) * 4.0
        + out["forecast_price_change_12m_pct"].fillna(0) * 2.0
        + market_signal_score
        + low_confidence_penalty
        + 20
    )

    document_penalty = np.where(out["document_ready"].astype(str).str.lower().isin(["true", "1", "yes"]), 0, 28)
    status_penalty = out["inbound_status"].map({"ON_TRACK": 0, "WATCH": 18, "AT_RISK": 35}).fillna(15)
    out["inbound_risk_score"] = _clip_score(
        out["inbound_delay_days"].fillna(0) * 4.0
        + customs_delay * 0.35
        + document_penalty
        + status_penalty
    )

    second_source_penalty = np.where(
        out["second_source_ready"].astype(str).str.lower().isin(["true", "1", "yes"]),
        0,
        35,
    )
    spec_penalty = np.where(out["spec_approved"].astype(str).str.lower().isin(["true", "1", "yes"]), 0, 25)
    safety_doc_penalty = np.where(
        out["food_safety_docs_ready"].astype(str).str.lower().isin(["true", "1", "yes"]),
        0,
        20,
    )
    out["second_source_risk_score"] = _clip_score(
        second_source_penalty + spec_penalty + safety_doc_penalty + origin_disruption * 0.30
    )

    out["total_risk_score"] = _clip_score(
        out["price_risk_score"] * 0.12
        + out["fx_freight_risk_score"] * 0.10
        + out["supply_risk_score"] * 0.13
        + out["shortage_risk_score"] * 0.20
        + out["landed_cost_risk_score"] * 0.15
        + out["forecast_risk_score"] * 0.12
        + out["inbound_risk_score"] * 0.10
        + out["second_source_risk_score"] * 0.08
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
