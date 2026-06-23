# model2 A/B/C 구조 전환 체크리스트

## 1) 지금 바로 할 일
1. 이 폴더의 `model2_pipeline/A_state`, `B_interpret`, `C_policy_action`를 기존 repo의 `model2_pipeline/` 아래로 복사
2. **아직 기존 파일은 지우지 말 것**
3. 기존 코드가 그대로 실행되는지 먼저 확인

## 2) 지금 당장 지우면 안 되는 기존 파일
아래 파일은 **새 import 경로로 모두 갈아탄 뒤**에만 archive/delete 가능

- `model2_pipeline/decision_generator.py`
- `model2_pipeline/model_features.py`
- `model2_pipeline/model_inference.py`
- `model2_pipeline/baseline_flow.py`
- `model2_pipeline/scenarios.py`
- `model2_pipeline/candidates.py`
- `model2_pipeline/gates.py`
- `model2_pipeline/simulation.py`
- `model2_pipeline/compare_select.py`
- `model2_pipeline/final_action.py`
- `model2_pipeline/pipeline.py`

## 3) 현재 권장 방식
- 1차: 새 A/B/C 폴더 추가 + 프록시 파일로 구조 먼저 잡기
- 2차: import 경로를 새 A/B/C 파일로 점진 교체
- 3차: 실행 확인 후 기존 파일을 `legacy_*`로 rename
- 4차: 마지막에만 실제 삭제

## 4) "지워도 되는 시점" 기준
아래 두 조건이 모두 만족될 때만 기존 파일 삭제 가능
- `run_all_in_one_hgb_pipeline.py` 및 `pipeline.py`가 더 이상 legacy 파일을 직접 import하지 않음
- 동일 입력으로 돌렸을 때 `final_decision_df`, `best_candidate_df`, `scenario_summary_df` 핵심 결과가 같음

## 5) 지금 단계의 진실
이 스캐폴드는 **구조를 먼저 세우는 단계**다.
즉시 로직을 완전 이동한 것이 아니라, "A/B/C 폴더와 파일 틀 + 안전한 프록시"를 먼저 만든 것이다.
