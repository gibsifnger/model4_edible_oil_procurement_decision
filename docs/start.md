# 최초실행시

터미널에서 폴더 위치가 model2_decision_pipeline인지 먼저 확인하고 실행

4단계. 가상환경 만들기
python -m venv .venv
.\.venv\Scripts\Activate.ps1

5단계. 패키지 설치
pip install -r requirements.txt


# 6단계. 처음 한 번 끝까지 돌려보기 (fresh-fit)
python run_all_in_one_hgb_pipeline.py --external-mode demo --fresh-fit

# 결과를 파일로 보고 싶으면
python run_all_in_one_hgb_pipeline.py --external-mode demo --fresh-fit --save-outputs --output-dir .\outputs

이때 돌아가는 흐름
- 외생 3개 demo 생성
- historical decision master 생성
- HGB quick-fit
- 최신 row scoring
- gate
- candidate
- scenario simulation
- final action 출력


# 저장된 artifact로 기본 실행
python run_all_in_one_hgb_pipeline.py --external-mode demo --model-a-path .\artifacts\target_a_hgb.joblib --model-b-path .\artifacts\target_b_hgb.joblib

항목              값
모델 소스          saved artifact
combine mode       rule_floor


# saved artifact로 결과를 파일 저장하면서 실행
python run_all_in_one_hgb_pipeline.py --external-mode demo --model-a-path .\artifacts\target_a_hgb.joblib --model-b-path .\artifacts\target_b_hgb.joblib --save-outputs --output-dir .\outputs


# demo + fresh-fit + rule_floor
python run_all_in_one_hgb_pipeline.py --external-mode demo --fresh-fit --prediction-combine-mode rule_floor --save-outputs --output-dir .\outputs


# saved artifact + model_only
python run_all_in_one_hgb_pipeline.py --external-mode demo --model-a-path .\artifacts\target_a_hgb.joblib --model-b-path .\artifacts\target_b_hgb.joblib --prediction-combine-mode model_only --save-outputs --output-dir .\outputs


# fresh-fit + rule_only
python run_all_in_one_hgb_pipeline.py --external-mode demo --fresh-fit --prediction-combine-mode rule_only --save-outputs --output-dir .\outputs