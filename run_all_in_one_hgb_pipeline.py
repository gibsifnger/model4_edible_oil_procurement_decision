"""모델2 올인원 실행 진입점.

핵심 목적
---------
1) 외생 3개 시계열을 준비한다.
   - demo 생성
   - 기존 CSV 로드
   - build_external_inputs.py 로 실데이터 월별 CSV 생성
2) hybrid decision master historical panel을 만든다.
3) target A/B용 HGB를 학습하거나, 저장된 artifact를 로드한다.
4) 최신 decision row를 score한다.
5) gate -> candidate -> scenario simulation -> final action까지 한 번에 실행한다.

중요
----
- '올인원'은 실행 파일이 1개라는 뜻이다.
- 내부 계산 로직은 model2_pipeline/* 분리 구조를 그대로 재사용한다.
- 따라서 모델1처럼 코드가 한 파일에 전부 뭉개지지 않는다.
"""
# 실행 코드  : python run_all_in_one_hgb_pipeline.py --start-month 2023-01-01 --demo-months 44 --decision-month 2026-04-01 --save-outputs

# 이 파일은 구매팀으로 치면 월간 구매회의 실행 순서표다.

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd
import math

# 2. 내부 모듈 import
# 이 부분은 구매팀장이 각 담당자에게 역할을 배정하는 것과 같다.
'''
담당	                코드
시장자료 담당	       build_external_inputs_monthly, make_demo_exogenous_df
월별 구매상태표 담당	build_hybrid_decision_master_df
위험/기회 판단 담당	    attach_target_predictions
정책 실행 담당	       run_full_decision_pipeline
'''
from build_external_inputs import ExternalBuildConfig, build_external_inputs_monthly
from model2_pipeline.config import PipelineConfig
from model2_pipeline.A_state.state_builder import (
    _ensure_monthly_exogenous_df,
    build_hybrid_decision_master_df,
    make_demo_exogenous_df,
)
from model2_pipeline.B_interpret.prediction_attach import (
    ModelBundle,
    attach_target_predictions,
    fit_demo_hgb_bundle,
    load_model_bundle,
    save_model_bundle,
)
from model2_pipeline.pipeline import run_full_decision_pipeline


# =========================================================
# 1) CLI
# =========================================================
# 3. parse_args() — 터미널 실행 옵션 정의
# 이 부분은 구매회의 전에 시장자료를 어디서 가져올지 정하는 부분이다.
'''
실행 옵션	              구매언어
--external-mode demo	실제 시장자료 없이 가상의 원당 가격/환율/운임 흐름으로 테스트
--external-mode csv	    내가 준비한 기존 월별 시장자료 CSV 사용
--external-mode build	외부 API와 운임 자료를 조합해서 월별 시장자료 새로 생성
'''
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run model2 all-in-one HGB decision pipeline")

    # external data mode
    parser.add_argument("--external-mode", choices=["demo", "csv", "build"], default="demo")
    parser.add_argument("--external-csv-path", default=None)
    parser.add_argument("--start-month", default="2019-01-01")
    parser.add_argument("--end-month", default=None)
    parser.add_argument("--demo-months", type=int, default=40)

    # build_external_inputs.py passthrough
    parser.add_argument("--ecos-api-key", default=None)
    parser.add_argument("--ecos-stat-code", default=None)
    parser.add_argument("--ecos-cycle", default="M")
    parser.add_argument("--ecos-item-code-1", default=None)
    parser.add_argument("--ecos-item-code-2", default=None)
    parser.add_argument("--ecos-item-code-3", default=None)
    parser.add_argument("--freight-mode", choices=["synthetic", "csv"], default="synthetic")
    parser.add_argument("--freight-csv-path", default=None)
    parser.add_argument("--freight-date-col", default="Date")
    parser.add_argument("--freight-value-col", default="freight_index")
    parser.add_argument("--built-external-output-csv", default=None)

# 4. 모델 저장본 사용 / 새 학습 옵션
# model artifact mode
     # 기본은 saved artifact 사용(아무 옵션 없이 실행 saved artifact 사용)
       # --fresh-fit 붙여서 실행시 fresh-fit 사용
    parser.set_defaults(use_saved_artifacts=True)

    parser.add_argument(
        "--use-saved-artifacts",
        dest="use_saved_artifacts",
        action="store_true",
        help="saved artifact 번들을 사용한다. (기본값)"
    )
    parser.add_argument(
        "--fresh-fit",
        dest="use_saved_artifacts",
        action="store_false",
        help="historical panel로 새로 quick-fit 한다."
    )
    parser.add_argument("--model-a-path", default=None)
    parser.add_argument("--model-b-path", default=None)
    parser.add_argument("--save-artifacts", action="store_true")
    parser.add_argument("--artifact-dir", default="./artifacts")
# 5. 예측 결합 방식 + 출력 옵션 + 판단월
# rule_floor는 보수적인 구매 의사결정에 가깝다.
# prediction combine mode
    parser.add_argument(
        "--prediction-combine-mode",
        choices=["auto", "model_only", "rule_floor", "rule_only"],
        default="rule_floor",
        help=(
            "최종 prediction 결합 방식. "
            "auto는 현재 rule_floor와 동일하게 처리. "
            "model_only: 모델 결과만 사용. "
            "rule_floor: max(rule, model). "
            "rule_only: 모델 무시하고 rule만 사용."
        ),
    )

    # output
    parser.add_argument("--save-outputs", action="store_true")
    parser.add_argument("--output-dir", default="./outputs")

    # misc
    parser.add_argument("--decision-month", default="2026-04-01") #월고정 
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


# =========================================================
# 2) External data loader / builder
# =========================================================
# 6. load_external_inputs_from_csv() — 외생변수 CSV 로드
def load_external_inputs_from_csv(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"external csv not found: {path}")

    df = pd.read_csv(path)
    df = _ensure_monthly_exogenous_df(df)
    return df

# 7. prepare_external_inputs() — demo/csv/build 중 하나로 외생변수 준비
# 실제 시장자료가 아직 없을 때, 테스트용으로 원당 가격/환율/운임 흐름을 가상 생성하는 것이다.
def prepare_external_inputs(args: argparse.Namespace) -> Tuple[pd.DataFrame, Optional[Path]]:
    """외생 3개 시계열 준비.

    Returns
    -------
    exogenous_df, built_csv_path
    """
    built_csv_path: Optional[Path] = None

#데모
    if args.external_mode == "demo":
        exogenous_df = make_demo_exogenous_df(
            start_month=args.start_month,
            n_months=args.demo_months,
            seed=args.seed,
        )
        exogenous_df = _ensure_monthly_exogenous_df(exogenous_df)
        return exogenous_df, built_csv_path
#csv
    if args.external_mode == "csv":
        if not args.external_csv_path:
            raise ValueError("--external-mode csv requires --external-csv-path")
        exogenous_df = load_external_inputs_from_csv(args.external_csv_path)
        return exogenous_df, built_csv_path
#build
    if args.external_mode == "build":
        build_cfg = ExternalBuildConfig(
            start_month=args.start_month,
            end_month=args.end_month,
            output_csv=args.built_external_output_csv or "external_inputs_monthly.csv",
            ecos_api_key=args.ecos_api_key,
            ecos_stat_code=args.ecos_stat_code,
            ecos_cycle=args.ecos_cycle,
            ecos_item_code_1=args.ecos_item_code_1,
            ecos_item_code_2=args.ecos_item_code_2,
            ecos_item_code_3=args.ecos_item_code_3,
            freight_mode=args.freight_mode,
            freight_csv_path=args.freight_csv_path,
            freight_date_col=args.freight_date_col,
            freight_value_col=args.freight_value_col,
            seed=args.seed,
        )
        exogenous_df = build_external_inputs_monthly(build_cfg)
        built_csv_path = Path(build_cfg.output_csv)
        exogenous_df.to_csv(built_csv_path, index=False, encoding="utf-8-sig")
        return exogenous_df, built_csv_path

    raise ValueError(f"unsupported external mode: {args.external_mode}")


# =========================================================
# 3) HGB fit / load
# =========================================================
# 8. fit_hgb_bundles_from_historical_panel() — historical panel로 A/B 모델 새 학습
# 과거 구매회의 기록을 보고 다음 질문에 답하는 기준을 학습하는 것이다.
'''
모델	구매 질문
target A	"이 상태면 재고 부족/긴급구매 위험인가?"
target B	"이 상태면 지금 사는 것이 원가상 유리하거나, 나중에 강제구매 리스크가 커지는가?"
'''
def fit_hgb_bundles_from_historical_panel(
    historical_master_df: pd.DataFrame,
    seed: int,
) -> Tuple[ModelBundle, ModelBundle]:
    """historical decision panel에서 target A/B용 HGB를 학습한다.

    학습 기준:
    - X: build_model_feature_frame(historical_master_df)
    - y_a: target_a_rule
    - y_b: target_b_rule

    주의:
    - 이 모델은 synthetic/hybrid historical row 위에 quick-fit 하는 데모/개발용 기본기다.
    - 나중에 실전 artifact가 있으면 --use-saved-artifacts 로 교체하면 된다.
    """
    if len(historical_master_df) < 5:
        raise ValueError("historical_master_df too short to fit HGB. Need at least 5 rows.")

    # 최신 row 1개는 live scoring 용도로 남겨두고, 앞 구간으로 학습
    train_df = historical_master_df.iloc[:-1].reset_index(drop=True).copy()

    from model2_pipeline.B_interpret.feature_builder import build_model_feature_frame

    X_train = build_model_feature_frame(train_df, cfg=PipelineConfig())
    y_a = train_df["target_a_rule"].astype(int)
    y_b = train_df["target_b_rule"].astype(int)

# 이건 과거 판단표를 기준으로 두 개의 판단기를 만드는 것이다.
    bundle_a = fit_demo_hgb_bundle(
        X=X_train,
        y=y_a,
        name="target_a_hgb",
        threshold=0.50,
        random_state=seed,
    )
    bundle_b = fit_demo_hgb_bundle(
        X=X_train,
        y=y_b,
        name="target_b_hgb",
        threshold=0.50,
        random_state=seed,
    )
    return bundle_a, bundle_b

# 9. resolve_model_bundles() — 저장 모델 로드 또는 새 학습 선택
# 검증된 기존 판단모델을 그대로 쓰는 방식이다.
 # 즉, "이번 구매회의에서는 이미 저장해 둔 위험/기회 판단기준을 사용하겠다"는 뜻이다
def resolve_model_bundles(
    historical_master_df: pd.DataFrame,
    args: argparse.Namespace,
) -> Tuple[ModelBundle, ModelBundle, str]:
    """저장 artifact 재사용 또는 quick-fit 중 하나를 선택."""
    if args.use_saved_artifacts:
        if not args.model_a_path or not args.model_b_path:
            raise ValueError("--use-saved-artifacts requires both --model-a-path and --model-b-path")
        bundle_a = load_model_bundle(args.model_a_path)
        bundle_b = load_model_bundle(args.model_b_path)
        return bundle_a, bundle_b, "loaded_saved_artifacts"

# 현재 만든 과거 판단표를 기준으로 이번 회의용 판단기준을 임시로 다시 만든다.
#냉정하게 말하면, 이건 실전 검증된 모델이라기보다 데모/개발용 빠른 학습 구조에 가깝다.
    bundle_a, bundle_b = fit_hgb_bundles_from_historical_panel(
        historical_master_df=historical_master_df,
        seed=args.seed,
    )

    if args.save_artifacts:
        artifact_dir = Path(args.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        save_model_bundle(bundle_a, artifact_dir / "target_a_hgb.joblib")
        save_model_bundle(bundle_b, artifact_dir / "target_b_hgb.joblib")

    return bundle_a, bundle_b, "fresh_fit_from_historical_panel"


# =========================================================
# 4) prediction combine mode
# =========================================================
# 10. resolve_prediction_combine_mode() — 예측 결합 방식 확정
 # "모델 판단과 규칙 판단 중 어떤 기준으로 최종 위험 판단을 할 것인가?"를 확정한다.
 # 현재 auto는 자동 최적화가 아니라 보수적으로 rule_floor를 쓰는 별칭이다.
def resolve_prediction_combine_mode(args: argparse.Namespace) -> str:
    """
    combine mode는 model source(fresh-fit / saved artifact)와 독립적으로 결정한다.
    auto는 현재 rule_floor와 동일하게 처리한다.
    """
    if args.prediction_combine_mode == "auto":
        return "rule_floor"
    return args.prediction_combine_mode

# =========================================================
# 4-1) Fixed canonical case: 2026-04
# =========================================================
# 11. _ceil_to_multiple() — 배수 올림 함수
# 발주량을 로트 단위에 맞추는 계산이다.
 # 예를 들어 최소 발주 단위가 2,500톤이면, 필요한 양이 5,300톤이어도 실제 발주는 7,500톤이 되어야 한다
def _ceil_to_multiple(value: float, multiple: float) -> float:
    if multiple <= 0:
        return float(value)
    return float(math.ceil(float(value) / float(multiple)) * float(multiple))

# 12. _recompute_fixed_case_helpers() — 2026-04 고정 케이스의 helper/rule 재계산
# "2026년 4월 말 구매회의"라는 특정 상황을 하나 잡고, 이 상황에 맞게 계산값을 다시 맞추는 단계다.
 # 즉, 여러 월의 일반 데이터 중에서 발표/시연용 대표 구매회의 케이스를 고정하는 구조다.
def _recompute_fixed_case_helpers(decision_df: pd.DataFrame) -> pd.DataFrame:
    """
    고정 케이스 row의 helper / rule을 다시 맞춘다.
    축:
    - 판단시점: 2026-04 말 구매회의
    - 영향 구간: 2026-07 ~ 2026-08 ~ 2026-09 초
    - 따라서 arrival_month_idx = 3 으로 둔다
      (m1=5월, m2=6월, m3=7월, m4=8월 기준)
    """
    out = decision_df.copy()
    if len(out) != 1:
        raise ValueError("_recompute_fixed_case_helpers expects exactly 1 row")

    row = out.iloc[0]
    

# 구매회의에서 가장 먼저 보는 기본판이다.

#질문	                               변수
#지금 창고에 쓸 수 있는 재고가 얼마인가?	current_inv
#최소로 얼마부터 살 수 있나?            moq
#발주는 몇 톤 단위로 끊어야 하나?	   lot
#지금 사면 톤당 얼마인가?	          now_cost
#지금 발주하면 몇 월부터 영향을 주나?	arrival_idx
#월별 사용량은 얼마인가?	              usage
#이미 들어올 물량은 얼마인가?	       open_po
#월별 예상 원가는 얼마인가?	           costs

    current_inv = float(row["current_inventory_ton"])
    moq = float(row["moq_ton"])
    lot = float(row["lot_multiple_ton"])
    now_cost = float(row["now_landed_cost_per_ton"])
    arrival_idx = int(row["candidate_arrival_month_idx"])

    usage = [
        float(row["usage_m1_ton"]),
        float(row["usage_m2_ton"]),
        float(row["usage_m3_ton"]),
        float(row["usage_m4_ton"]),
    ]
    open_po = [
        float(row["open_po_m1_ton"]),
        float(row["open_po_m2_ton"]),
        float(row["open_po_m3_ton"]),
        float(row["open_po_m4_ton"]),
    ]
    costs = [
        float(row["expected_landed_cost_m1_per_ton"]),
        float(row["expected_landed_cost_m2_per_ton"]),
        float(row["expected_landed_cost_m3_per_ton"]),
        float(row["expected_landed_cost_m4_per_ton"]),
    ]

# 13. baseline no-buy world — 아무것도 안 샀을 때 재고/부족 계산
    # -------------------------
    # baseline no-buy world
    # -------------------------
    inv = current_inv
    ending_inv_path = []
    shortage_path = []

    for i in range(4):
        available = inv + open_po[i]
        shortage = max(usage[i] - available, 0.0)
        end_inv = max(available - usage[i], 0.0)

        shortage_path.append(shortage)
        ending_inv_path.append(end_inv)
        inv = end_inv

    baseline_total_shortage_ton = float(sum(shortage_path))

# 14. 첫 부족월 + 도착 직전 재고 계산
    first_shortage_month_idx = None
    for i, s in enumerate(shortage_path, start=1):
        if s > 0:
            first_shortage_month_idx = i
            break

    # arrival 직전 재고
    if arrival_idx <= 1:
        begin_inv_at_arrival_ton = current_inv
    else:
        begin_inv_at_arrival_ton = float(ending_inv_path[arrival_idx - 2])


#15. 도착 시점 기준 필요 구매량 계산
    cum_usage = 0.0
    cum_open_po = 0.0
    max_cum_gap_arrival_ton = 0.0

    start_idx = max(arrival_idx - 1, 0)
    for i in range(start_idx, 4):
        cum_usage += usage[i]
        cum_open_po += open_po[i]
        gap = cum_usage - cum_open_po - begin_inv_at_arrival_ton
        max_cum_gap_arrival_ton = max(max_cum_gap_arrival_ton, gap)

    max_cum_gap_arrival_ton = max(0.0, float(max_cum_gap_arrival_ton))

    if max_cum_gap_arrival_ton > 0:
        required_buy_qty_arrival_ton = max_cum_gap_arrival_ton + moq
    else:
        required_buy_qty_arrival_ton = 0.0

# 16. 첫 부족월 기준 필요 구매량 계산
    if first_shortage_month_idx is not None:
        if first_shortage_month_idx == 1:
            begin_inv_first_shortage = current_inv
        else:
            begin_inv_first_shortage = float(ending_inv_path[first_shortage_month_idx - 2])

        first_idx0 = first_shortage_month_idx - 1
        first_gap = max(
            usage[first_idx0] - open_po[first_idx0] - begin_inv_first_shortage,
            0.0,
        )
        required_buy_qty_first_shortage_ton = first_gap + moq if first_gap > 0 else 0.0
    else:
        required_buy_qty_first_shortage_ton = 0.0


# 17. B helper — 원가상승 압력 계산
    # -------------------------
    # B helper (원가상승 압력)
    # -------------------------
    peak_cost = max(costs)
    b_peak_cost_vs_now_pct = (peak_cost / now_cost - 1.0) if now_cost > 0 else 0.0
    b_peak_cost_vs_now_pct = float(max(b_peak_cost_vs_now_pct, 0.0))

    b_forced_buy_flag = int(b_peak_cost_vs_now_pct >= 0.06)
    b_forced_buy_cost_vs_now_pct = b_peak_cost_vs_now_pct
    b_emergency_premium_score = round(b_peak_cost_vs_now_pct * 1000.0, 6)
    b_high_cost_month_count = int(sum(1 for c in costs if c > now_cost * 1.03))

# 18. helper/rule overwrite — 계산값을 row에 다시 넣기
    # -------------------------
    # 최종 helper / rule overwrite
    # -------------------------
    out.loc[:, "baseline_total_shortage_ton"] = baseline_total_shortage_ton
    out.loc[:, "a_first_shortage_month_idx"] = (
        float(first_shortage_month_idx) if first_shortage_month_idx is not None else float("nan")
    )
    out.loc[:, "a_emergency_buy_needed_flag"] = int(baseline_total_shortage_ton > 0)
    out.loc[:, "a_min_end_inv_ton"] = float(min(ending_inv_path))
    out.loc[:, "a_min_cover_months"] = 0.0

    out.loc[:, "begin_inv_at_arrival_ton"] = begin_inv_at_arrival_ton
    out.loc[:, "max_cum_gap_arrival_ton"] = max_cum_gap_arrival_ton
    out.loc[:, "required_buy_qty_arrival_ton"] = required_buy_qty_arrival_ton
    out.loc[:, "required_buy_qty_first_shortage_ton"] = required_buy_qty_first_shortage_ton

    out.loc[:, "b_peak_cost_vs_now_pct"] = b_peak_cost_vs_now_pct
    out.loc[:, "b_forced_buy_flag"] = b_forced_buy_flag
    out.loc[:, "b_forced_buy_cost_vs_now_pct"] = b_forced_buy_cost_vs_now_pct
    out.loc[:, "b_emergency_premium_score"] = b_emergency_premium_score
    out.loc[:, "b_high_cost_month_count"] = b_high_cost_month_count

    # rule은 fixed case 기준으로 다시 부여
    out.loc[:, "target_a_rule"] = int(baseline_total_shortage_ton > 0)
    out.loc[:, "target_b_rule"] = int(b_peak_cost_vs_now_pct >= 0.08)

    return out

# 19. apply_fixed_case_2026_04() — 2026년 4월 구매회의 케이스 강제 고정
# "2026년 4월 30일 구매회의에서 원당을 볼 때"라는 장면을 만든다.
def apply_fixed_case_2026_04(decision_df: pd.DataFrame) -> pd.DataFrame:
    """
    최초 가정사항 고정:
    - 구매회의: 2026년 4월 말
    - 지금 발주 시 실질 영향: 7월 ~ 8월 ~ 9월 초
    - 따라서 arrival_month_idx = 3 (7월 도착 영향으로 해석)
    """
    out = decision_df.copy()
    if len(out) != 1:
        raise ValueError("apply_fixed_case_2026_04 expects exactly 1 row")

    # -------------------------
    # 시점 고정
    # -------------------------
    out.loc[:, "decision_id"] = "RAW_SUGAR_2026-04"
    out.loc[:, "decision_month"] = "2026-04-01"
    out.loc[:, "as_of_month"] = pd.Timestamp("2026-04-01")
    out.loc[:, "decision_date"] = pd.Timestamp("2026-04-30")

    # -------------------------
    # 리드타임 / 도착축
    # 4월말 회의 -> 7월부터 본격 영향
    # -------------------------
    out.loc[:, "lt_months"] = 3
    out.loc[:, "candidate_arrival_month_idx"] = 3

    # -------------------------
    # 재고 / 운영 제약
    # -------------------------
    out.loc[:, "on_hand_inventory_ton"] = 50000.0
    out.loc[:, "blocked_inventory_ton"] = 2000.0
    out.loc[:, "usable_inventory_ton"] = 48000.0
    out.loc[:, "current_inventory_ton"] = 48000.0

    out.loc[:, "moq_ton"] = 5000.0
    out.loc[:, "lot_multiple_ton"] = 2500.0
    out.loc[:, "warehouse_capacity_ton"] = 55000.0
    out.loc[:, "working_capital_pressure_score"] = 62.0

    # -------------------------
    # 현재 원가 / horizon 원가 경로
    # -------------------------
    out.loc[:, "now_landed_cost_per_ton"] = 648.0
    out.loc[:, "expected_landed_cost_m1_per_ton"] = 652.0
    out.loc[:, "expected_landed_cost_m2_per_ton"] = 658.0
    out.loc[:, "expected_landed_cost_m3_per_ton"] = 669.0
    out.loc[:, "expected_landed_cost_m4_per_ton"] = 676.0

    # -------------------------
    # 수요: 5월, 6월, 7월, 8월
    # 7~8월을 높게 둬서 네 최초 가정과 맞춘다
    # -------------------------
    out.loc[:, "usage_m1_ton"] = 23000.0
    out.loc[:, "usage_m2_ton"] = 24000.0
    out.loc[:, "usage_m3_ton"] = 29000.0
    out.loc[:, "usage_m4_ton"] = 28000.0

    # -------------------------
    # 기존 오더: 6월/7월 일부만 들어오는 상황
    # -------------------------
    out.loc[:, "open_po_m1_ton"] = 0.0
    out.loc[:, "open_po_m2_ton"] = 5000.0
    out.loc[:, "open_po_m3_ton"] = 2500.0
    out.loc[:, "open_po_m4_ton"] = 0.0

    # shock flag 현재월은 off
    if "shock_event_flag_now" in out.columns:
        out.loc[:, "shock_event_flag_now"] = 0

    # helper / rule 재계산
    out = _recompute_fixed_case_helpers(out)
    return out

# =========================================================
# 5) Main all-in-one runner
# =========================================================
# 20. run_all_in_one_pipeline() — 전체 실행 본체
 # 구매회의를 실행하기 전에 회사 기준표와 시장자료를 준비하는 단계다.
def run_all_in_one_pipeline(args: argparse.Namespace) -> Dict[str, pd.DataFrame]:
    cfg = PipelineConfig()

    # 1. 외생 3개 준비
    exogenous_df, built_csv_path = prepare_external_inputs(args)

    # 2. historical decision master 생성
    historical_master_df = build_hybrid_decision_master_df(
        exogenous_df=exogenous_df,
        cfg=cfg,
        seed=args.seed,
        keep_latest_only=False,
    )
    if historical_master_df.empty:
        raise ValueError("historical_master_df is empty after generation.")

    # 3. HGB bundle 확보 (load or fit)
    bundle_a, bundle_b, model_mode = resolve_model_bundles(
        historical_master_df=historical_master_df,
        args=args,
    )

    combine_mode = resolve_prediction_combine_mode(args)

    # 4. 지정 판단월 row 선택 + fixed case overwrite + score
    decision_month = pd.to_datetime(args.decision_month).to_period("M").to_timestamp()

    latest_decision_df = (
        historical_master_df[
            pd.to_datetime(historical_master_df["decision_month"]).dt.to_period("M").dt.to_timestamp()
            == decision_month
        ]
        .tail(1)
        .reset_index(drop=True)
    )

    if latest_decision_df.empty:
        available_months = (
            pd.to_datetime(historical_master_df["decision_month"])
            .dt.to_period("M")
            .astype(str)
            .drop_duplicates()
            .tolist()
        )
        raise ValueError(
            f"decision_month {decision_month.date()} not found in historical_master_df. "
            f"available decision_months tail={available_months[-12:]}"
        )

# 22. fixed case 보정 + A/B 예측값 부착
# "2026년 4월 말 원당 구매회의 상황을 고정하고, 이 상황이 A 관점에서 수급 위험인지, 
# B 관점에서 원가/기회 위험인지 판단값을 붙인다."
    latest_decision_df = apply_fixed_case_2026_04(latest_decision_df)

    scored_latest_df = attach_target_predictions(
        decision_master_df=latest_decision_df,
        cfg=cfg,
        model_a_bundle=bundle_a,
        model_b_bundle=bundle_b,
        fallback_to_rule=False,
        combine_mode=combine_mode,
    )

    # 5. final decision pipeline
    pipeline_outputs = run_full_decision_pipeline(
        decision_master_df=scored_latest_df,
        cfg=cfg,
    )
# 24. meta_df와 outputs 구성
 # 구매회의 결과 보고서에 붙는 실행 요약과 부속 표를 묶는 단계다.
    # 6. 메타 정보
    meta_df = pd.DataFrame([
        {
            "external_mode": args.external_mode,
            "model_mode": model_mode,
            "prediction_combine_mode": combine_mode,
            "historical_rows": len(historical_master_df),
            "latest_decision_month": scored_latest_df.loc[0, "decision_month"],
            "built_external_csv": str(built_csv_path) if built_csv_path else "",
            "save_artifacts": int(bool(args.save_artifacts)),
            "use_saved_artifacts": int(bool(args.use_saved_artifacts)),
        }
    ])

    outputs: Dict[str, pd.DataFrame] = {
        "meta_df": meta_df,
        "exogenous_df": exogenous_df,
        "historical_master_df": historical_master_df,
        "scored_latest_df": scored_latest_df,
        **pipeline_outputs,
    }

    if args.save_outputs:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, df in outputs.items():
            df.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")

    return outputs


# =========================================================
# 6) Console display
# =========================================================
# 25. 콘솔 출력 보조 함수들
def _fmt_value(v):
    if pd.isna(v):
        return "-"
    if isinstance(v, (int, float)):
        if abs(float(v)) >= 1000:
            return f"{float(v):,.0f}"
        if float(v).is_integer():
            return f"{int(v)}"
        return f"{float(v):,.4f}"
    return str(v)

# 26. print_key_outputs() — 핵심 결과 콘솔 표시
def _print_kv_block(title: str, items: Dict[str, object]) -> None:
    print(f"\n[{title}]")
    for k, v in items.items():
        print(f"- {k}: {_fmt_value(v)}")


def _safe_row(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    return df.iloc[0]


def _print_df(title: str, df: pd.DataFrame, columns: list[str] | None = None, sort_by: list[str] | None = None) -> None:
    print(f"\n[{title}]")
    if df is None or df.empty:
        print("(empty)")
        return

    out = df.copy()
    if sort_by:
        existing_sort = [c for c in sort_by if c in out.columns]
        if existing_sort:
            out = out.sort_values(existing_sort).reset_index(drop=True)

    if columns:
        existing_cols = [c for c in columns if c in out.columns]
        out = out[existing_cols]

    print(out.to_string(index=False))


def print_key_outputs(outputs: Dict[str, pd.DataFrame]) -> None:
    meta = _safe_row(outputs.get("meta_df", pd.DataFrame()))
    latest = _safe_row(outputs.get("scored_latest_df", pd.DataFrame()))
    best = _safe_row(outputs.get("best_candidate_df", pd.DataFrame()))
    final = _safe_row(outputs.get("final_decision_df", pd.DataFrame()))

    _print_kv_block(
        "run_summary",
        {
            "external_mode": meta.get("external_mode"),
            "model_mode": meta.get("model_mode"),
            "prediction_combine_mode": meta.get("prediction_combine_mode"),
            "latest_decision_month": meta.get("latest_decision_month"),
            "historical_rows": meta.get("historical_rows"),
        },
    )

    _print_df(
        "exogenous_tail",
        outputs.get("exogenous_df", pd.DataFrame()).tail(4),
        columns=["as_of_month", "global_raw_sugar_price", "usdkrw", "freight_index"],
    )

    _print_kv_block(
        "latest_purchase_state",
        {
            "decision_month": latest.get("decision_month"),
            "decision_date": latest.get("decision_date"),
            "lt_months": latest.get("lt_months"),
            "candidate_arrival_month_idx": latest.get("candidate_arrival_month_idx"),
            "usable_inventory_ton": latest.get("usable_inventory_ton"),
            "now_landed_cost_per_ton": latest.get("now_landed_cost_per_ton"),
            "working_capital_pressure_score": latest.get("working_capital_pressure_score"),
            "a_first_shortage_month_idx": latest.get("a_first_shortage_month_idx"),
            "baseline_total_shortage_ton": latest.get("baseline_total_shortage_ton"),
            "max_cum_gap_arrival_ton": latest.get("max_cum_gap_arrival_ton"),
            "required_buy_qty_arrival_ton": latest.get("required_buy_qty_arrival_ton"),
            "required_buy_qty_first_shortage_ton": latest.get("required_buy_qty_first_shortage_ton"),
            "target_a_final_pred": latest.get("target_a_final_pred"),
            "target_b_final_pred": latest.get("target_b_final_pred"),
        },
    )

    _print_df(
        "month_path_m1_to_m4",
        pd.DataFrame([
            {
                "usage_m1_ton": latest.get("usage_m1_ton"),
                "open_po_m1_ton": latest.get("open_po_m1_ton"),
                "expected_landed_cost_m1_per_ton": latest.get("expected_landed_cost_m1_per_ton"),
                "usage_m2_ton": latest.get("usage_m2_ton"),
                "open_po_m2_ton": latest.get("open_po_m2_ton"),
                "expected_landed_cost_m2_per_ton": latest.get("expected_landed_cost_m2_per_ton"),
                "usage_m3_ton": latest.get("usage_m3_ton"),
                "open_po_m3_ton": latest.get("open_po_m3_ton"),
                "expected_landed_cost_m3_per_ton": latest.get("expected_landed_cost_m3_per_ton"),
                "usage_m4_ton": latest.get("usage_m4_ton"),
                "open_po_m4_ton": latest.get("open_po_m4_ton"),
                "expected_landed_cost_m4_per_ton": latest.get("expected_landed_cost_m4_per_ton"),
            }
        ]),
    )

    _print_df(
        "candidate_summary",
        outputs.get("candidate_df", pd.DataFrame()),
        columns=[
            "candidate_name",
            "candidate_qty_ton",
            "candidate_arrival_month_idx",
            "candidate_unit_cost_per_ton_now",
            "candidate_po_value_now",
            "required_buy_qty_arrival_ton",
            "baseline_total_shortage_ton",
        ],
    )

    _print_df(
        "gated_candidate_summary",
        outputs.get("gated_candidate_df", pd.DataFrame()),
        columns=[
            "candidate_name",
            "candidate_qty_ton",
            "candidate_status",
            "moq_gate_pass",
            "lot_multiple_gate_pass",
            "warehouse_gate_pass",
            "working_capital_gate_result",
            "arrival_timing_gate_result",
            "projected_total_shortage_ton_base",
            "hard_fail_reason",
            "soft_warning_reason",
        ],
    )

    scenario_df = outputs.get("scenario_summary_df", pd.DataFrame()).copy()
    if not scenario_df.empty:
        scenario_df = scenario_df[
            [
                "candidate_name",
                "scenario_name",
                "candidate_status",
                "total_shortage_ton",
                "min_ending_inventory_ton",
                "total_cost",
                "cost_vs_observe_pct",
            ]
        ].sort_values(["candidate_name", "scenario_name"]).reset_index(drop=True)
    _print_df("scenario_summary_compact", scenario_df)

    _print_kv_block(
        "selected_candidate",
        {
            "selected_candidate_name": best.get("selected_candidate_name"),
            "selected_candidate_qty_ton": best.get("selected_candidate_qty_ton"),
            "selected_candidate_status": best.get("selected_candidate_status"),
            "selected_worst_case_shortage_ton": best.get("selected_worst_case_shortage_ton"),
            "selected_worst_case_cost_vs_observe_pct": best.get("selected_worst_case_cost_vs_observe_pct"),
            "required_candidate_qty_ton": best.get("required_candidate_qty_ton"),
            "required_candidate_status": best.get("required_candidate_status"),
            "required_candidate_hard_fail_reason": best.get("required_candidate_hard_fail_reason"),
        },
    )

    _print_kv_block(
        "final_action_summary",
        {
            "need_buy_flag": final.get("need_buy_flag"),
            "selected_candidate_name": final.get("selected_candidate_name"),
            "selected_candidate_qty_ton": final.get("selected_candidate_qty_ton"),
            "final_action": final.get("final_action"),
            "final_reason": final.get("final_reason"),
            "additional_check_reason": final.get("additional_check_reason"),
        },
    )



if __name__ == "__main__":
    args = parse_args()
    outputs = run_all_in_one_pipeline(args)
    print_key_outputs(outputs)
