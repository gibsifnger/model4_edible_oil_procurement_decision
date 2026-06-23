#A/B 타깃에 대해 모델 예측을 만들고, 그 예측을 룰과 어떤 관계로 최종 확정할지 결정하는 연결층

from __future__ import annotations
from dataclasses import dataclass #ModelBundle 정의
from pathlib import Path #저장 경로 이름 처리
from typing import Any, List
import joblib #모델 번들 저장/로드
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier #실제 분류 모델

from ..config import PipelineConfig #현재 원가 컬럼명 등 설정 전달
from .feature_builder import build_model_feature_frame #모델 입력 X 생성

#“예측기 하나만 들고 다니지 말고, 이 예측기를 회사에서 어떻게 써야 하는지 정보까지 같이 묶자는 구조다.”
@dataclass
class ModelBundle:
    model: Any #실제 학습된 분류기
    feature_cols: List[str] #이 모델이 기대하는 입력 열 순서/목록
    threshold: float #확률을 0/1로 자를 기준값
    name: str #모델 이름

#데모용 HGB 번들 학습
  # X, y를 받아 HistGradientBoostingClassifier를 학습하고, 학습된 모델을 ModelBundle 형태로 반환한다.
def fit_demo_hgb_bundle(X: pd.DataFrame, y: pd.Series, name: str, threshold: float = 0.50, random_state: int = 42) -> ModelBundle:
    model = HistGradientBoostingClassifier(
        max_depth=4,#트리깊이 제한
        learning_rate=0.08, #학습률
        max_iter=150, #부스팅반복회수
        min_samples_leaf=4, #리프 최소샘플수
        random_state=random_state, #재현성확보(기본42)
    )
    model.fit(X, y)
    return ModelBundle(model=model, feature_cols=list(X.columns), threshold=float(threshold), name=name)

# 모델 번들 저장(ModelBundle을 그대로 저장하지 않고, 딕셔너리 payload로 풀어 joblib.dump로 저장한다))
def save_model_bundle(bundle: ModelBundle, path: str | Path) -> None:
    payload = {
        "model": bundle.model,
        "feature_cols": bundle.feature_cols,
        "threshold": bundle.threshold,
        "name": bundle.name,
    }
    joblib.dump(payload, path)

# 모델 번들 로드(저장된 payload를 읽어서 다시 ModelBundle로 복원한다. 여기서 get(..., default)를 써서
  # 일부 정보가 없어도 기본값으로 복원한다) “예전에 저장한 A/B 타깃 예측기를 다시 불러와서
    # 운영 파이프라인에 붙일 수 있게 만든다.”
def load_model_bundle(path: str | Path) -> ModelBundle:
    payload = joblib.load(path)
    return ModelBundle(
        model=payload["model"],
        feature_cols=list(payload["feature_cols"]),
        threshold=float(payload.get("threshold", 0.50)),
        name=str(payload.get("name", Path(path).stem)),
    )

# _predict_binary: 확률 → 이진판정 (threshold는 0.5로 일단 정함?)
def _predict_binary(bundle: ModelBundle, X: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    X_use = X.reindex(columns=bundle.feature_cols, fill_value=0.0)
    proba = pd.Series(bundle.model.predict_proba(X_use)[:, 1], index=X.index, dtype=float)
    pred = (proba >= float(bundle.threshold)).astype(int) 
    return proba, pred

# attach_target_predictions 함수 선언 + 모드 설명
# “이 장면을 A 구매 시그널로 볼지, B 구매 시그널로 볼지를 모델과 룰을 이용해 최종 운영용 표식으로 붙이는 단계다.”
    """target A/B prediction attach.
    combine_mode
    ------------
    - model_only : final_pred = model_pred
    - rule_floor : final_pred = max(rule, model_pred)
    - rule_only  : final_pred = rule
    """
    #여기서 combine mode를 바꾸면 달라지는건지? 
def attach_target_predictions(
    decision_master_df: pd.DataFrame,
    cfg: PipelineConfig,
    model_a_bundle: ModelBundle,
    model_b_bundle: ModelBundle,
    fallback_to_rule: bool = False,
    combine_mode: str = "model_only",
) -> pd.DataFrame:

# 출력용 복사본 만들기 + rule 컬럼 확보(“룰 판정이 아예 없는 상황도 있을 수 있으니,
  # 그때는 룰 신호 없음(0) 으로 놓고 계속 간다.”)
    out = decision_master_df.copy()

    # rule columns가 없으면 0으로 둔다.
    target_a_rule = out.get("target_a_rule", pd.Series(0, index=out.index)).fillna(0).astype(int)
    target_b_rule = out.get("target_b_rule", pd.Series(0, index=out.index)).fillna(0).astype(int)
    out["target_a_rule"] = target_a_rule
    out["target_b_rule"] = target_b_rule
    
# fallback_to_rule=True일 때: 모델을 완전히 우회
  # “모델이 불안정하거나 아직 준비 안 된 상황이면, 일단 회사 룰만으로 운영하되 출력 양식은 동일하게 유지하자.”
    # 회사 룰은 model2_pipeline/B_interpret/helper_calculator.py에 있다. 
    if fallback_to_rule:
        out["target_a_proba"] = target_a_rule.astype(float)
        out["target_b_proba"] = target_b_rule.astype(float)
        out["target_a_model_pred"] = target_a_rule.astype(int)
        out["target_b_model_pred"] = target_b_rule.astype(int)
        out["target_a_pred"] = target_a_rule.astype(int)
        out["target_b_pred"] = target_b_rule.astype(int)
        out["target_a_final_pred"] = target_a_rule.astype(int)
        out["target_b_final_pred"] = target_b_rule.astype(int)
        out["target_a_final_source"] = "rule_only"
        out["target_b_final_source"] = "rule_only"
        return out

# 공식 feature frame 생성 + A/B 모델 예측
    X = build_model_feature_frame(out, cfg=cfg)
    target_a_proba, target_a_model_pred = _predict_binary(model_a_bundle, X)
    target_b_proba, target_b_model_pred = _predict_binary(model_b_bundle, X)
# 모델 결과를 out에 붙이기
    out["target_a_proba"] = target_a_proba
    out["target_b_proba"] = target_b_proba
    out["target_a_model_pred"] = target_a_model_pred.astype(int)
    out["target_b_model_pred"] = target_b_model_pred.astype(int)

# 기존 downstream 호환용 pred는 model pred로 유지
    out["target_a_pred"] = out["target_a_model_pred"]
    out["target_b_pred"] = out["target_b_model_pred"]

# “회사가 이 운영 구간에서는 룰보다 모델판단을 그대로 믿겠다는 설정이다.”
    if combine_mode == "model_only":
        out["target_a_final_pred"] = out["target_a_model_pred"].astype(int)
        out["target_b_final_pred"] = out["target_b_model_pred"].astype(int)
        out["target_a_final_source"] = "model_only"
        out["target_b_final_source"] = "model_only"
# 룰과 모델 예측을 나란히 붙인 뒤 행별 최대값을 취한다. 즉, 둘 중 하나라도 1이면 최종은 1이다.        
    elif combine_mode == "rule_floor":
        out["target_a_final_pred"] = pd.concat([target_a_rule, out["target_a_model_pred"]], axis=1).max(axis=1).astype(int)
        out["target_b_final_pred"] = pd.concat([target_b_rule, out["target_b_model_pred"]], axis=1).max(axis=1).astype(int)
        out["target_a_final_source"] = "rule_floor"
        out["target_b_final_source"] = "rule_floor"
# “모델은 참고로 돌려보되, 실제 운영 표식은 아직 회사 룰만 믿겠다는 설정이다.”
    elif combine_mode == "rule_only":
        out["target_a_final_pred"] = target_a_rule.astype(int)
        out["target_b_final_pred"] = target_b_rule.astype(int)
        out["target_a_final_source"] = "rule_only"
        out["target_b_final_source"] = "rule_only"
    else:
        raise ValueError(f"unsupported combine_mode: {combine_mode}")

# “이제 이 행은 A/B 타깃 관점에서 모델상 확률, 모델판정, 최종 운영판정, 그 출처까지 다 붙은 상태로 다음 단계로 넘어간다.”
    return out
