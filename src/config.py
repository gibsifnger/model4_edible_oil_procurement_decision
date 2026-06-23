from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "edible_oil_market_inputs_demo.csv"
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "edible_oil_purchase_decision_summary.csv"


@dataclass(frozen=True)
class ProcurementConfig:
    """Business thresholds used by the edible oil purchase decision pipeline."""

    safety_stock_month: float = 1.2
    target_cover_month: float = 2.5
    urgent_cover_month: float = 0.9
    lead_time_buffer_month: float = 0.5
    high_risk_threshold: float = 70.0
    medium_risk_threshold: float = 45.0
    moq_tolerance: float = 0.85


DEFAULT_CONFIG = ProcurementConfig()
