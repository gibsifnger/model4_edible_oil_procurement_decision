"""
[FILE PURPOSE]
- 이 파일은 model4 유지 구매 의사결정 파이프라인의 실행 진입점이다.
- 직접 구매 로직을 계산하는 파일이 아니라, CSV 입력부터 상태 생성, 리스크 해석,
  구매 액션 추천, output 저장까지 한 번에 연결하는 wrapper 역할을 한다.

[BUSINESS UNIT]
- 롯데웰푸드 원물/소싱팀의 유지 품목 구매 판단 단위.
- 팜유/대두유/해바라기유/야자유 같은 수입 유지 원료의 발주 검토,
  입항/통관 일정 확인, 구매 회의용 요약표 생성을 모델링한다.

[INPUT]
- data/edible_oil_market_inputs_demo.csv
- 현재고, Open PO, 월 사용량, 리드타임, MOQ, Landed Cost 구성 요소,
  forecast signal, ETD/ETA, 통관 예정일, 2nd Source 준비 여부를 읽는다.

[OUTPUT]
- outputs/edible_oil_purchase_decision_summary.csv
- 품목별 inventory_cover_month, required_cover_month, risk_score,
  primary_action, follow_up_action, reason_text가 포함된 구매 판단 요약표.

[현업 적용 시 교체 대상]
- demo CSV는 SAP MM 구매오더, ERP 재고, WMS 가용재고, 포워더 ETD/ETA,
  통관 상태, 보세창고 입고일, 원재료 시황, 환율/운임, 협력사 MOQ,
  Spec 승인 및 식품안전 서류 상태 데이터로 대체해야 한다.
"""

from __future__ import annotations

from src.config import DATA_PATH, OUTPUT_PATH
from src.pipeline import run_pipeline


# ============================================================
# [BLOCK] CLI 실행 진입점
# [업무 의미] 구매 담당자가 python run_pipeline.py 한 번으로 판단표를 생성하는 구간.
# [판단 기준] 세부 계산은 src 모듈에 위임하고, 여기서는 표준 입력/출력 경로만 연결한다.
# [산출물] outputs/edible_oil_purchase_decision_summary.csv 및 콘솔 요약.
# [수정 포인트] 현업에서는 배치 스케줄러, ERP export, BI 리포트 버튼과 연결될 수 있다.
# [WHY] 면접/포트폴리오에서 "실행 가능한 의사결정 파이프라인"임을 보여주는 진입점이다.
# [ASSUMPTION] 현재는 synthetic/demo CSV가 이미 준비되어 있다고 가정한다.
# [DESIGN LOGIC] 계산 로직을 run 파일에 두지 않아 A/B/C 구조를 유지한다.
# [DATA LINEAGE] data/edible_oil_market_inputs_demo.csv -> src.pipeline.run_pipeline.
# [REAL DATA REPLACEMENT] 사내 데이터마트 또는 SAP/WMS export 경로를 DATA_PATH로 연결한다.
# [INTERVIEW CHECK] 이 파일은 모델이 아니라 운영 실행 wrapper라고 설명하면 된다.
# ============================================================
def main() -> None:
    result_df = run_pipeline(DATA_PATH, OUTPUT_PATH)
    print(f"Saved purchase decision summary: {OUTPUT_PATH}")
    print(
        result_df[
            ["item_name", "primary_action", "follow_up_action", "total_risk_score"]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
