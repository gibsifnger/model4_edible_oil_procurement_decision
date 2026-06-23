# Model4 Edible Oil Procurement Decision

롯데웰푸드 원물/소싱팀의 유지 구매관리 포지션을 가정한 구매 의사결정 MVP입니다.

이 모델은 롯데웰푸드 AI 구매 어시스턴트 같은 시황 예측값을 대체하는 것이 아니라, 예측값을 실제 구매 실행 기준으로 연결하는 구매 의사결정 파이프라인입니다. 핵심은 원재료 가격 예측 자체가 아니라, 시황/환율/운임/재고/Open PO/입항/통관/2nd Source/Landed Cost를 한 표에서 보고 `BUY_NOW`, `SPLIT_ORDER`, `WAIT`, `CHECK` 계열 액션으로 변환하는 것입니다.

데이터는 실제 회사 데이터가 아닌 synthetic/demo 데이터입니다. 결과는 구매 업무 사고방식과 데이터 파이프라인 구조를 보여주기 위한 예시이며, 실거래 의사결정에 바로 사용할 수 없습니다.

## 업무 연결

| 업무 영역 | 파이프라인 반영 방식 |
|---|---|
| 원부자재 구매계획/발주 | 재고 커버, 리드타임, 안전재고, MOQ 기준으로 구매 필요성을 판단 |
| 시황 분석 | 가격 변화, forecast market signal, 환율/운임 리스크를 구매 액션으로 연결 |
| 원가율 방어 | `landed_cost_krw_per_ton`, 관세, 통관비, 내륙운송비, 창고비를 반영 |
| 협력사 관리 | 공급 안정성, 주 공급사, 2nd Source 준비 여부를 점검 |
| 수입/통관 | ETD, ETA, 서류 준비, 통관 예정일, 국내 입고 예정일, 지연 일수를 반영 |
| 데이터 분석 | CSV 입력에서 상태, 리스크, 액션, reason_text까지 추적 가능한 결과 생성 |

## 의사결정 흐름

```text
data/edible_oil_market_inputs_demo.csv
  -> A_state: 현재 구매상태 생성
  -> B_interpret: 위험/기회 신호 해석
  -> C_policy_action: 구매 액션 추천
  -> outputs/edible_oil_purchase_decision_summary.csv
```

## A_state

현재 구매 상태를 만듭니다.

| 컬럼 | 의미 |
|---|---|
| `landed_cost_krw_per_ton` | 계약가, 관세, 통관비, 내륙운송비, 창고비를 반영한 톤당 도착원가 |
| `inbound_status` | Open PO 입고 일정 상태: `ON_TRACK`, `WATCH`, `AT_RISK` |
| `effective_available_inventory_ton` | 입고 리스크를 반영한 실질 가용 재고 |
| `at_risk_open_po_ton` | 입고 리스크가 있는 Open PO 물량 |
| `required_cover_month` | `lead_time_month + safety_stock_month` |
| `shortage_expected` | 필요 커버 개월 수 대비 재고 부족 여부 |

## B_interpret

구매 판단에 필요한 리스크 점수를 계산합니다.

| 컬럼 | 의미 |
|---|---|
| `price_risk_score` | 최근 가격 상승과 변동성 리스크 |
| `fx_freight_risk_score` | 환율 및 운임 리스크 |
| `supply_risk_score` | 공급사 신뢰도, 산지 리스크, 통관 리스크 |
| `shortage_risk_score` | 재고 커버와 리드타임 부족 리스크 |
| `landed_cost_risk_score` | Landed Cost 상승 부담 |
| `forecast_risk_score` | 3개월/12개월 전망과 forecast market signal |
| `inbound_risk_score` | ETD/ETA/서류/통관/입고 지연 리스크 |
| `second_source_risk_score` | 2nd Source, 규격 승인, 식품안전 서류 준비 리스크 |
| `total_risk_score` | 위 점수를 가중합한 종합 리스크 |

## C_policy_action

최종 추천은 실행 우선순위를 분리합니다.

| 컬럼 | 의미 |
|---|---|
| `primary_action` | 지금 가장 먼저 취할 구매 액션 |
| `follow_up_action` | 함께 확인해야 하는 후속 액션 |
| `recommended_action` | 기존 호환성을 위한 컬럼이며 `primary_action`과 동일 |
| `recommended_order_ton` | 즉시 구매 또는 분할 구매 시 권장 발주량 |
| `reason_text` | 구매 담당자가 읽을 수 있는 판단 근거 |

액션 후보는 다음과 같습니다.

- `BUY_NOW`
- `SPLIT_ORDER`
- `WAIT`
- `NEGOTIATE_PRICE`
- `CHECK_SUPPLIER`
- `CHECK_CUSTOMS_RISK`
- `SECOND_SOURCE_REVIEW`
- `INBOUND_SCHEDULE_CHECK`
- `LANDED_COST_REVIEW`

## 결과 예시

아래 표는 demo 데이터 기준 예시입니다. 실제 실행 결과는 `outputs/edible_oil_purchase_decision_summary.csv`에서 확인할 수 있습니다.

| item_name | origin_country | inventory_cover_month | required_cover_month | total_risk_score | risk_level | primary_action | follow_up_action |
|---|---|---:|---:|---:|---|---|---|
| 해바라기유 | 우크라이나 | 0.77 | 4.20 | 90.1 | HIGH | BUY_NOW | INBOUND_SCHEDULE_CHECK |
| 팜유 | 말레이시아 | 0.92 | 3.20 | 70.9 | HIGH | SPLIT_ORDER | INBOUND_SCHEDULE_CHECK |
| 야자유 | 필리핀 | 1.50 | 3.20 | 62.9 | MEDIUM | INBOUND_SCHEDULE_CHECK | CHECK_CUSTOMS_RISK |
| 대두유 | 미국 | 3.00 | 3.70 | 42.4 | LOW | WAIT | NONE |

## 폴더 구조

```text
model4_edible_oil_procurement_decision
├─ README.md
├─ requirements.txt
├─ run_pipeline.py
├─ data/
│  └─ edible_oil_market_inputs_demo.csv
├─ outputs/
│  ├─ .gitkeep
│  └─ edible_oil_purchase_decision_summary.csv
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

## 데이터 정합성 메모

- Landed Cost는 demo CSV의 `usd_krw`를 우선 사용해 계산합니다.
- 실제 적용 시 `usd_krw`는 ERP 기준환율, 수입신고 기준환율, 계약일 환율, 선적일 환율 등 회사 기준에 맞춰 교체할 수 있습니다.
- `planned_order_ton`이 `supplier_moq_ton`보다 작으면 실제 발주 가능 수량이 아니므로, 공급사 MOQ 협의, 수량 조정, 묶음 선적, 또는 관망 판단이 필요합니다.
- 이 모델은 가격 예측 모델이 아니라 가격/환율/운임/재고/Open PO/통관/MOQ/Landed Cost를 구매 액션으로 변환하는 의사결정 파이프라인입니다.

## 입력 품목

데모 데이터에는 유지 구매관리 관점의 네 가지 품목이 포함됩니다.

- 팜유
- 대두유
- 해바라기유
- 야자유

## 이력서 3줄 요약

- 유지 원물 구매 업무를 가정해 재고, Open PO, 입항/통관 일정, 2nd Source, Landed Cost를 통합한 구매 의사결정 파이프라인을 구축했습니다.
- AI 시황 예측값을 대체하는 모델이 아니라, forecast signal을 실제 구매 실행 기준과 연결해 `BUY_NOW`, `SPLIT_ORDER`, `WAIT`, `CHECK` 액션으로 변환했습니다.
- synthetic/demo 데이터를 기반으로 A_state, B_interpret, C_policy_action 3단계 구조와 한글 reason_text를 구현해 구매 실무형 의사결정 흐름을 보여주었습니다.
