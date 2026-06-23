# Model4 Edible Oil Procurement Decision

롯데웰푸드 원물/소싱팀의 유지 구매관리 업무를 가정한 구매 의사결정 지원 파이프라인입니다. 이 프로젝트는 가격을 맞히는 예측 모델이 아니라, 시장 입력과 운영 제약을 바탕으로 지금 구매 상태를 만들고 위험/기회 신호를 해석한 뒤 실행 가능한 구매 액션을 추천하는 MVP입니다.

데이터는 실제 회사 데이터가 아닌 synthetic/demo 데이터입니다. 따라서 결과는 업무 사고방식과 데이터 파이프라인 구조를 보여주기 위한 예시이며, 실거래 의사결정에 바로 사용할 수 없습니다.

## 업무 연결

이 파이프라인은 채용공고의 주요 업무와 다음처럼 연결됩니다.

- 원부자재 구매계획/발주: 품목별 재고 커버, 리드타임 갭, MOQ 충족 여부로 발주 필요성을 판단합니다.
- 시황 분석: 가격 변동, 환율, 운임, 산지 리스크를 점수화해 구매 타이밍 판단 근거로 사용합니다.
- 협력사 관리/단가협상: 공급 안정성, MOQ 미달, 가격 리스크를 기반으로 협력사 확인 또는 단가 협상 액션을 분리합니다.
- 수급/재고관리: 결품 예상 여부와 재고 커버 개월 수를 중심으로 BUY_NOW, SPLIT_ORDER, WAIT를 추천합니다.
- 수입/통관: 통관 지연 리스크가 높은 품목은 CHECK_CUSTOMS_RISK로 별도 점검합니다.
- 데이터 분석: CSV 입력에서 상태, 리스크, 액션, reason_text까지 추적 가능한 테이블을 생성합니다.

## 결과 예시

아래는 demo 데이터 기준으로 생성되는 유지 구매 의사결정 결과 예시입니다.

| 품목 | 재고커버개월 | 종합 리스크 점수 | 추천 액션 | 판단 요약 |
|---|---:|---:|---|---|
| 팜유 | 1.0 | 82 | SPLIT_ORDER | 가격·환율·운임 부담이 높고 재고커버가 낮아 분할 발주 검토 |
| 대두유 | 3.0 | 38 | WAIT | 재고 여유가 있고 단기 수급 리스크가 낮아 관망 가능 |
| 해바라기유 | 0.9 | 78 | CHECK_SUPPLIER | 재고커버가 낮고 공급 안정성 확인이 필요 |
| 야자유 | 1.5 | 71 | CHECK_CUSTOMS_RISK | 통관 지연 가능성이 있어 선적·통관 일정 우선 확인 필요 |

> 실제 수치는 demo/synthetic 데이터 기준이며, 실거래 단가·실제 공급사 정보는 포함하지 않았습니다.


## 의사결정 흐름

```text
data/edible_oil_market_inputs_demo.csv
  -> A_state: 현재 구매상태 생성
  -> B_interpret: 위험/기회 신호 해석
  -> C_policy_action: 구매 액션 추천
  -> outputs/edible_oil_purchase_decision_summary.csv
```

### A_state

- `inventory_cover_month`: 현재 재고와 예정 입고가 몇 개월 수요를 커버하는지 계산
- `lead_time_gap_month`: 리드타임 대비 재고 커버 부족분 계산
- `shortage_expected`: 리드타임과 안전 버퍼를 고려한 결품 예상 여부
- `moq_check`: 계획 발주량이 공급사 MOQ 기준을 만족하는지 확인

### B_interpret

- `price_risk_score`
- `fx_freight_risk_score`
- `supply_risk_score`
- `shortage_risk_score`
- `total_risk_score`

### C_policy_action

추천 액션은 다음 중 하나입니다.

- `BUY_NOW`: 결품 위험이 커서 즉시 구매
- `SPLIT_ORDER`: 가격/환율/운임 변동성이 높아 분할 구매
- `WAIT`: 현재는 관망
- `NEGOTIATE_PRICE`: 단가 협상 또는 견적 재확인
- `CHECK_SUPPLIER`: 협력사 납기/대체 공급처 확인
- `CHECK_CUSTOMS_RISK`: 선적/서류/통관 지연 위험 확인

각 추천에는 사람이 읽을 수 있는 `reason_text`가 함께 생성됩니다.

## 폴더 구조

```text
model4_edible_oil_procurement_decision
├─ README.md
├─ requirements.txt
├─ run_pipeline.py
├─ data/
│  └─ edible_oil_market_inputs_demo.csv
├─ outputs/
│  └─ .gitkeep
└─ src/
   ├─ config.py
   ├─ A_state.py
   ├─ B_interpret.py
   ├─ C_policy_action.py
   └─ pipeline.py
```

## 실행 방법

```bash
pip install -r requirements.txt
python run_pipeline.py
```

실행 후 결과 파일이 생성됩니다.

```text
outputs/edible_oil_purchase_decision_summary.csv
```

## 입력 품목

데모 데이터에는 유지 구매관리 관점의 네 가지 품목이 포함됩니다.

- 팜유
- 대두유
- 해바라기유
- 야자유

## 이력서 3줄 요약

- 유지 원물 구매 업무를 가정해 재고, 리드타임, MOQ, 가격/환율/운임, 공급/통관 리스크를 통합한 구매 의사결정 파이프라인을 구축했습니다.
- 기존 복잡한 모델 구조를 A_state, B_interpret, C_policy_action 3단계로 재설계해 상태 생성, 리스크 해석, 실행 액션 추천 흐름을 명확히 했습니다.
- synthetic/demo 데이터를 활용해 BUY_NOW, SPLIT_ORDER, WAIT, NEGOTIATE_PRICE, CHECK_SUPPLIER, CHECK_CUSTOMS_RISK 액션과 한글 설명 문구를 자동 생성했습니다.
