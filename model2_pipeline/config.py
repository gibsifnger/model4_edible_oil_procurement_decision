# 이 파일은 엔진이 아니라 기준표다. 그래서 없어지면 계산 로직이 바로 사라지는 느낌은 아니지만, 
# 실제로는 거의 모든 생성기/해석기/정책기가 여기 숫자에 매달려 있어서 사실상 모델의 공통 기준 본체

from __future__ import annotations

# 블록 1) PipelineConfig 선언
 # “회사 구매정책 기준표를 하나의 객체로 묶어두고, 모든 생성기가 그 기준표를 공유해서 읽는다.”
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class PipelineConfig:
    # 블록 2) 자재/리드타임/버퍼/수평기간 설정
     # “원당을 대상으로, 발주 후 2개월 뒤 도착하는 기본 가정으로, 앞으로 4개월 horizon에서 판단하겠다.”
    material_code: str = "RAW_SUGAR"
    lt_months: int = 2
    buffer_months: int = 2
    horizon_months: int = 4
    # 블록 3) 운영 제약값 설정
     # “우리는 기본적으로 월 26,500톤 수준을 쓰고, 최소 5,000톤 단위로 2,500톤 배수로 발주하며, 
     # 창고는 55,000톤까지 보고, 안전재고는 5,000톤으로 본다.”
    monthly_usage_base_ton: float = 26_500.0
    moq_ton: float = 5_000.0
    lot_multiple_ton: float = 2_500.0
    warehouse_capacity_ton: float = 55_000.0
    safety_stock_ton: float = 5_000.0
   # 블록 4) 운전자본 게이트 임계값 설정
    # “운전자본 압박 점수가 70 이상이면 주의가 필요하고, 90 이상이면 아예 막는 기준으로 보겠다.”
    wc_pressure_conditional_threshold: float = 70.0
    wc_pressure_block_threshold: float = 90.0
   # 블록 5) 현재단가 컬럼명 설정
    # “현재 구매단가는 now_landed_cost_per_ton 컬럼을 공식 현재단가로 쓰겠다.”
    current_landed_cost_col: str = "now_landed_cost_per_ton"

   # 블록 6) scenario_library 선언
   # “기준세계(base), 빡빡한 세계(stress), 충격세계(shock)를 따로 두고, 
   # 각 세계에서 수요/원가/도착지연/긴급매입 프리미엄을 다르게 보겠다.”
    scenario_library: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "base": {
            "demand_mult": 1.00,
            "cost_mult": 1.00,
            "open_po_delay_months": 0,
            "candidate_delay_months": 0,
            "emergency_premium_mult": 1.00,
        },
        "stress": {
            "demand_mult": 1.05,
            "cost_mult": 1.03,
            "open_po_delay_months": 0,
            "candidate_delay_months": 0,
            "emergency_premium_mult": 1.10,
        },
        "shock": {
            "demand_mult": 1.12,
            "cost_mult": 1.08,
            "open_po_delay_months": 1,
            "candidate_delay_months": 1,
            "emergency_premium_mult": 1.25,
        },
    })
