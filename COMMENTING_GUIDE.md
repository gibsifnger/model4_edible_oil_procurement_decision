# Model4 Commenting Guide

이 문서는 `model4_edible_oil_procurement_decision`의 주석 작성 기준입니다. 목적은 코드 문법 설명이 아니라, 유지 원물 구매관리 업무에서 각 로직이 어떤 의사결정을 모델링하는지 설명하는 것입니다.

## 기본 원칙

- 가격 예측 모델이 아니라 구매 의사결정 파이프라인이라는 관점을 유지합니다.
- forecast signal은 대체 대상이 아니라 구매 실행 액션으로 변환할 입력값으로 설명합니다.
- 단순 계약 단가보다 Landed Cost 기준 판단을 강조합니다.
- Open PO는 전체를 재고로 보지 않고 입항/통관 리스크를 반영한 가용재고로 설명합니다.
- 주석은 `pandas를 불러온다` 같은 문법 설명이 아니라, 구매/SCM/수입 업무 의미를 설명합니다.

## 파일 상단 Docstring

각 Python 파일 상단에는 다음 항목을 포함합니다.

- `[FILE PURPOSE]`: 파일이 A/B/C 파이프라인에서 맡는 역할
- `[BUSINESS UNIT]`: 품목, 산지, 공급사, 구매 판단 단위
- `[INPUT]`: 주요 입력 컬럼과 데이터 출처
- `[OUTPUT]`: 다음 단계로 넘기는 산출 컬럼
- `[현업 적용 시 교체 대상]`: demo 데이터를 실제 SAP/WMS/통관/시황 데이터로 바꿀 때 필요한 출처

## 블록 주석

주요 함수와 로직 블록 앞에는 다음 형식을 사용합니다.

```python
# ============================================================
# [BLOCK] 블록명
# [업무 의미] 이 블록이 구매/수입 구매 업무에서 의미하는 것
# [판단 기준] 어떤 구매 기준을 반영하는지
# [산출물] 다음 단계로 넘기는 컬럼/테이블
# [수정 포인트] 현업 적용 시 회사별로 바꿀 기준
# [WHY] 이 로직이 필요한 이유
# [ASSUMPTION] synthetic/demo 데이터 기반 가정
# [DESIGN LOGIC] 값과 조건을 설계한 원리
# [DATA LINEAGE] 직접/간접 반영 CSV 컬럼
# [REAL DATA REPLACEMENT] 실제 대체 데이터
# [INTERVIEW CHECK] 면접에서 방어할 설명 포인트
# ============================================================
```

## 조건문 주석

`if`, `np.where`, action mapping, risk level mapping에는 조건이 어떤 업무 판단으로 연결되는지 적습니다.

예시:

```python
# 결품 위험과 긴급 재고 부족이 동시에 있으면 가격보다 생산 차질 방어가 우선이므로 BUY_NOW.
if row["shortage_expected"] and row["inventory_cover_month"] <= cfg.urgent_cover_month:
    return "BUY_NOW"
```

## 면접 설명 키워드

- 가격 예측 모델 X, 구매 의사결정 파이프라인 O
- forecast signal 대체 X, 구매 실행 액션 변환 O
- 단가 기준 X, Landed Cost 기준 O
- Open PO 전체 반영 X, 입항/통관 리스크 반영 가용재고 O
- 결과값은 PO 자동 생성이 아니라 구매 회의용 판단표
