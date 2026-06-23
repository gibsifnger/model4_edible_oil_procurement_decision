# refactor_notes.md

이 패키지는 model2를 A/B/C 구조로 나누기 위한 1차 안전 리팩토링본이다.

포함 내용:
- `model2_pipeline/A_state/*` 추가
- `model2_pipeline/B_interpret/*` 추가
- `model2_pipeline/C_policy_action/*` 추가
- `run_all_in_one_hgb_pipeline.py` import 경로를 A/B/C wrapper 기준으로 변경
- `pipeline.py` import 경로를 A/B/C wrapper 기준으로 변경
- `compare_select.py`, `final_action.py`의 need_buy_flag 계산을 B 공통 신호로 통일
- `A_state/state_builder.py`와 `B_interpret/helper_calculator.py`는 legacy proxy가 아니라 실제 코드 복사 분리본

의도적으로 유지한 것:
- `decision_generator.py` legacy 보존
- `simulation.py`, `gates.py`, `candidates.py` 로직 보존
- 결과 스키마/컬럼명 보존 우선
