# Model2 Procurement Decision Pipeline

구매/SCM 의사결정을 위한 end-to-end 파이프라인입니다. 단순 가격 예측 모델이 아니라, 외생 데이터로 현재 구매 상태를 만들고, 위험과 기회를 해석한 뒤, 회사 운영 기준에 맞는 정책 후보를 비교해 최종 액션을 추천하는 구조입니다.

## What This Project Shows

이 프로젝트는 구매 담당자가 반복적으로 마주치는 질문을 데이터 파이프라인으로 구조화합니다.

- 지금 구매 장면은 어떤 상태인가?
- 위험 또는 기회로 판단하는 근거는 무엇인가?
- 실행 가능한 구매 정책 후보는 무엇인가?
- 회사의 MOQ, 창고, 운전자본, 입고 시점 기준을 통과하는가?
- 여러 시나리오에서 어떤 후보가 가장 견고한가?
- 최종적으로 구매, 관망, 추가 확인 중 무엇을 선택해야 하는가?

## Decision Architecture

### A. State Layer

현재 구매 장면과 운영 상태를 생성합니다.

- 외생 데이터 입력
- 월별 기준 상태 생성
- baseline no-buy 흐름 계산
- shortage helper 및 재고 흐름 구성

### B. Interpret Layer

상태를 판단 가능한 신호로 해석합니다.

- 모델 feature 생성
- Target A / Target B scoring
- 필요 구매 신호 해석
- 위험/기회 판단 근거 구성

### C. Policy Action Layer

회사 기준으로 실행 가능한 액션을 선택합니다.

- 후보 정책 생성
- 운영 gate 판단
- 시나리오 시뮬레이션
- 후보 비교와 최종 액션 추천

## Pipeline Flow

```text
External inputs
  -> State generation
  -> Judgment interpretation
  -> Candidate policy generation
  -> Operating gate check
  -> Scenario simulation
  -> Robust candidate selection
  -> Final action recommendation
```

## Repository Structure

```text
.
├─ README.md
├─ requirements.txt
├─ .gitignore
├─ run_all_in_one_hgb_pipeline.py
├─ build_external_inputs.py
├─ data/
│  ├─ actual_external_inputs_monthly_2023_01_to_2026_08_working.csv
│  └─ actual_external_inputs_monthly_2023_01_to_2026_08_notes.txt
├─ docs/
│  ├─ model2_column_map_and_candidate_patch.md
│  ├─ model2_dataflow_dictionary.xlsx
│  ├─ refactor_notes.md
│  ├─ migration_delete_checklist.md
│  ├─ rearrange.md
│  └─ start.md
├─ outputs/
│  └─ .gitkeep
├─ artifacts/
│  └─ .gitkeep
└─ model2_pipeline/
   ├─ A_state/
   ├─ B_interpret/
   ├─ C_policy_action/
   ├─ config.py
   └─ pipeline.py
```

## Main Scripts

### `run_all_in_one_hgb_pipeline.py`

전체 의사결정 파이프라인을 실행하는 메인 스크립트입니다.

지원 모드:

- `--external-mode demo`
- `--external-mode csv`
- `--external-mode build`

주요 옵션:

- `--decision-month`
- `--demo-months`
- `--external-csv-path`
- `--save-outputs`
- `--save-artifacts`
- `--use-saved-artifacts`
- `--prediction-combine-mode`

### `build_external_inputs.py`

구매 판단에 필요한 월별 외생 입력 데이터를 구성하는 스크립트입니다.

주요 입력 축:

- 원재료 가격
- 환율
- 운임 지수

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run Demo Mode

처음 실행할 때는 저장된 모델 artifact가 없을 수 있으므로 `--fresh-fit`을 사용합니다. Demo mode의 현재 예시 데이터는 `2022-04-01`까지의 decision month를 포함합니다.

```bash
python run_all_in_one_hgb_pipeline.py --external-mode demo --demo-months 44 --decision-month 2022-04-01 --save-outputs --fresh-fit
```

### 3. Run CSV Mode

```bash
python run_all_in_one_hgb_pipeline.py --external-mode csv --external-csv-path data/actual_external_inputs_monthly_2023_01_to_2026_08_working.csv --decision-month 2026-04-01 --save-outputs --fresh-fit
```

저장된 모델 artifact를 재사용하려면 `--model-a-path`와 `--model-b-path`를 함께 지정합니다.

## Key Outputs

`--save-outputs` 옵션을 사용하면 `outputs/`에 주요 DataFrame이 CSV로 저장됩니다.

- `meta_df`
- `exogenous_df`
- `historical_master_df`
- `scored_latest_df`
- `baseline_flow_df`
- `candidate_df`
- `gated_candidate_df`
- `simulation_result_df`
- `scenario_summary_df`
- `robust_summary_df`
- `best_candidate_df`
- `final_decision_df`

## Portfolio Perspective

이 프로젝트는 구매/SCM 업무를 다음 세 가지 관점으로 분리해 보여줍니다.

- 상태 생성: 외생 변수와 운영 데이터를 구매 판단 가능한 상태로 변환
- 판단 해석: 예측 결과와 rule signal을 결합해 위험/기회 근거 구성
- 정책 행동: 실행 가능한 후보만 gate로 걸러내고 시나리오 기반으로 최종 액션 선택

따라서 결과물은 "예측값" 하나가 아니라, 구매 의사결정에 필요한 상태, 근거, 후보, 제약, 시뮬레이션, 추천 액션을 함께 제공합니다.
