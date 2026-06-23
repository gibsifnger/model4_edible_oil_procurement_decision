"""
[FILE PURPOSE]
- 유지 구매 의사결정 파이프라인에서 공통으로 쓰는 기준값과 파일 경로를 관리한다.
- 구매 판단 기준을 코드 곳곳에 흩뿌리지 않고 한 곳에서 관리하도록 만든 설정 파일이다.

[BUSINESS UNIT]
- 유지 품목 × 산지 × 공급사 × 구매 판단 기준.
- 리드타임, 안전재고, MOQ 허용 기준, 리스크 등급 구간처럼 구매팀이 조정할 수 있는
  정책 값을 표현한다.

[INPUT]
- 외부 파일을 직접 읽지는 않지만 run_pipeline.py와 src.pipeline.py가 사용할
  demo 입력 경로와 output 저장 경로를 제공한다.

[OUTPUT]
- ProcurementConfig 객체.
- safety_stock_month, target_cover_month, risk threshold, moq_tolerance,
  default_usdkrw 같은 구매 판단 기준값.

[현업 적용 시 교체 대상]
- safety stock, risk threshold, MOQ 허용 범위는 SAP MM 품목마스터,
  구매 정책서, 서비스레벨 정책, 공급사별 계약 조건에 맞춰 조정해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = PROJECT_ROOT / "data" / "edible_oil_market_inputs_demo.csv"
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "edible_oil_purchase_decision_summary.csv"


# ============================================================
# [BLOCK] 구매 판단 기준값
# [업무 의미] 재고가 충분한지, 리스크가 높은지, MOQ를 충족하는지 판단하는 기준표.
# [판단 기준] 리드타임+안전재고, 리스크 점수 구간, MOQ 허용 범위를 하나로 모은다.
# [산출물] A_state, B_interpret, C_policy_action에서 참조하는 ProcurementConfig.
# [수정 포인트] 품목별 서비스레벨, 공급 변동성, 창고 정책에 따라 값만 조정한다.
# [WHY] 기준값을 한 곳에 모아야 면접에서 "정책값과 로직을 분리했다"고 설명할 수 있다.
# [ASSUMPTION] demo에서는 모든 유지 품목에 같은 기준값을 적용한다.
# [DESIGN LOGIC] dataclass(frozen=True)로 실행 중 기준값이 우발적으로 바뀌지 않게 한다.
# [DATA LINEAGE] config 값은 demo CSV 컬럼과 결합되어 상태/리스크/액션 계산에 반영된다.
# [REAL DATA REPLACEMENT] 실제로는 품목마스터, 구매 정책, 공급사 계약 조건으로 대체한다.
# [INTERVIEW CHECK] 수치는 정답이 아니라 업무 기준을 외부화한 예시라고 설명한다.
# ============================================================
@dataclass(frozen=True)
class ProcurementConfig:
    """Business thresholds used by the edible oil purchase decision pipeline."""

    safety_stock_month: float = 1.2  # 리드타임 외에 추가로 확보할 안전재고 개월 수.
    target_cover_month: float = 2.5  # 권장 발주량 산정 시 맞추려는 목표 재고 커버.
    urgent_cover_month: float = 0.9  # 이 값 이하이면 결품 임박으로 보고 BUY_NOW를 우선 검토.
    lead_time_buffer_month: float = 0.5  # 리드타임 오차를 흡수하기 위한 보수적 버퍼.
    high_risk_threshold: float = 70.0  # 구매 회의에서 즉시 액션 검토가 필요한 HIGH 리스크 구간.
    medium_risk_threshold: float = 45.0  # 모니터링과 후속 확인이 필요한 MEDIUM 리스크 구간.
    moq_tolerance: float = 0.85  # 계획 물량이 MOQ에 근접했는지 판단하는 허용 비율.
    default_usdkrw: float = 1380.0  # demo CSV에 환율 컬럼이 없을 때 Landed Cost 계산에 쓰는 기본 환율.


DEFAULT_CONFIG = ProcurementConfig()
