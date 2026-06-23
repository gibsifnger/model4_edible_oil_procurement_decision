"""Hybrid decision master 생성기.

역할:
- 업로드한 synthetic rules v1의 핵심 개념을 코드로 옮겨,
  외생변수 3개(global_raw_sugar_price, usdkrw, freight_index) + synthetic 운영변수로
  decision master를 만드는 파일이다.

이 파일이 필요한 이유:
- 기존 final decision layer는 이미 helper / target / horizon wide plan이 붙은
  decision_master_df를 전제로 한다.
- 실제로는 그 decision master를 먼저 만들어야 HGB scoring과 final action이 이어진다.

주의:
- 여기 구현은 production-like mini generator다.
- historical row 기준 helper/label은 future realized path를 써서 만들 수 있지만,
  live 현재월 의사결정에는 forecasted horizon path로 교체하는 것이 맞다.
"""

from __future__ import annotations

from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from .config import PipelineConfig

# 블록 1) 외생 입력 최소 형태 강제
# “결국 이 모델은 월별 판단을 하니까, 외생 입력도 월별로 정리된 표여야 한다. 
# 그리고 원당/환율/운임 3개는 반드시 있어야 한다.”
def _ensure_monthly_exogenous_df(exogenous_df: pd.DataFrame) -> pd.DataFrame:
    required = ["as_of_month", "global_raw_sugar_price", "usdkrw", "freight_index"]
    missing = [c for c in required if c not in exogenous_df.columns]
    if missing:
        raise ValueError(f"exogenous_df missing required columns: {missing}")

    out = exogenous_df.copy()
    out["as_of_month"] = pd.to_datetime(out["as_of_month"]).dt.to_period("M").dt.to_timestamp()
    out = out.sort_values("as_of_month").drop_duplicates("as_of_month", keep="last").reset_index(drop=True)
    return out

# 블록 2) 데모용 외생 시계열 생성기
# “실제 데이터가 없어도 ‘그럴듯한 월별 시장환경’을 만들어서 모델 전체를 검증할 수 있게 하자.”
def make_demo_exogenous_df(
    start_month: str = "2023-01-01",
    n_months: int = 36,
    seed: int = 42,
) -> pd.DataFrame:
    """실행 가능한 end-to-end demo용 외생 월별 시계열 생성."""
    rng = np.random.default_rng(seed)
    months = pd.date_range(start=start_month, periods=n_months, freq="MS")
    t = np.arange(n_months)

    sugar = 430 + 25 * np.sin(2 * np.pi * t / 12) + np.cumsum(rng.normal(0, 4, n_months))
    usdkrw = 1280 + 35 * np.sin(2 * np.pi * (t + 2) / 9) + np.cumsum(rng.normal(0, 6, n_months))
    freight = 105 + 10 * np.sin(2 * np.pi * (t + 1) / 6) + np.cumsum(rng.normal(0, 2, n_months))

    # shock month 2~4회 배치
    shock_count = int(rng.integers(2, 5))
    shock_idx = sorted(rng.choice(np.arange(4, n_months - 4), size=shock_count, replace=False).tolist())
    for idx in shock_idx:
        sugar[idx: min(idx + 2, n_months)] += rng.uniform(18, 35)
        usdkrw[idx: min(idx + 2, n_months)] += rng.uniform(25, 55)
        freight[idx: min(idx + 2, n_months)] += rng.uniform(8, 15)

    out = pd.DataFrame({
        "as_of_month": months,
        "global_raw_sugar_price": np.round(sugar, 2),
        "usdkrw": np.round(usdkrw, 2),
        "freight_index": np.round(freight, 2),
    })

    out["shock_event_flag"] = 0
    out.loc[shock_idx, "shock_event_flag"] = 1
    return out

# 블록 3) landed cost proxy와 계절/판촉/월초월말 요인 함수들
# “시장 데이터는 그대로는 의사결정에 못 쓰니, 현재 원가 proxy와 월별 수요 패턴 보정값으로 변환한다.”
def _calc_landed_cost_proxy(sugar: float, usdkrw: float, freight: float) -> float:
    """원당 landed cost proxy.
    scale만 맞춘 mini proxy이며, live에서는 실제 내부 원가식/정규화 식으로 교체 가능.
    """
    return float(0.72 * sugar + 0.18 * usdkrw + 1.10 * freight)


def _month_seasonality(month_num: int) -> float:
    mapping = {
        1: 0.96, 2: 0.95, 3: 0.99, 4: 1.00,
        5: 1.02, 6: 1.05, 7: 1.08, 8: 1.10,
        9: 1.06, 10: 1.02, 11: 0.99, 12: 0.98,
    }
    return float(mapping[int(month_num)])


def _promo_factor(month_num: int, shock_flag: int, rng: np.random.Generator) -> float:
    promo_peak = {6, 7, 8, 9}
    base = 0.04 if int(month_num) in promo_peak else 0.0
    random_bump = rng.choice([0.0, 0.0, 0.03, 0.05])
    shock_offset = -0.01 if int(shock_flag) == 1 else 0.0
    return float(base + random_bump + shock_offset)


def _bom_factor(month_num: int, rng: np.random.Generator) -> float:
    base = 0.02 if int(month_num) in {3, 4, 10, 11} else 0.0
    return float(base + rng.choice([0.0, 0.0, 0.01]))

# 블록 4) 미래 사용량 path 생성
# “앞으로 몇 달 동안 실제로 얼마를 쓸 것 같은지를 계절/판촉/월초월말/노이즈를 섞어서 만든다.”
def _make_usage_path(
    future_months: pd.Series,
    future_shock_flags: pd.Series,
    cfg: PipelineConfig,
    rng: np.random.Generator,
) -> List[float]:
    values: List[float] = []
    for month_ts, shock_flag in zip(future_months, future_shock_flags):
        seasonality = _month_seasonality(pd.Timestamp(month_ts).month)
        promo = _promo_factor(pd.Timestamp(month_ts).month, int(shock_flag), rng)
        bom = _bom_factor(pd.Timestamp(month_ts).month, rng)
        noise = rng.normal(0.0, 0.015)
        usage = cfg.monthly_usage_base_ton * seasonality * (1.0 + promo + bom + noise)
        values.append(float(max(18000.0, usage)))
    return values

# 블록 5) 기존 open PO path 생성
# “이미 잡혀 있는 예정입고가 horizon 동안 어느 정도 들어올지를 간단한 skeleton으로 만든다.”
def _make_open_po_path(
    current_inventory_ton: float,
    current_month_idx: int,
    cfg: PipelineConfig,
) -> List[float]:
    """기존 open PO / 예정입고 skeleton.
    의도:
    - LT 앞단에 이미 잡혀 있는 예정입고만 일부 반영
    - later month는 기존 PO 약화
    """
    base_qty = cfg.moq_ton
    inv_stress = current_inventory_ton < (cfg.monthly_usage_base_ton * 0.9)

    path: List[float] = []
    for month_idx in range(1, cfg.horizon_months + 1):
        if month_idx == 1:
            qty = base_qty if inv_stress else cfg.lot_multiple_ton
        elif month_idx == 2:
            qty = base_qty
        elif month_idx == 3:
            qty = cfg.lot_multiple_ton if current_month_idx % 2 == 0 else 0.0
        else:
            qty = 0.0
        path.append(float(qty))
    return path

# 블록 6) arrival 기준 필요구매수량 계산
# “도착월에 한 번 넣는다고 가정했을 때, 진짜로 얼마를 사야 이후 horizon에서 안전재고를 유지할 수 있는지를 계산한다.”
def _calc_required_buy_qty_from_arrival(
    starting_inventory_ton: float,
    usage_path: List[float],
    open_po_path: List[float],
    arrival_month_idx: int,
    safety_stock_ton: float,
) -> Dict[str, float]:
    """arrival 시점 주입 수량 requirement 계산.

    의미
    ----
    - arrival 이전은 no-buy world 그대로 간다.
    - arrival month에 수량 Q가 한 번 들어온다고 가정했을 때,
      이후 모든 월의 ending inventory가 safety stock 이상이 되려면 필요한 최소 Q를 구한다.
    - 이 값이 후보 생성의 shortage_anchored 핵심 기준이 된다.
    """
    begin_inv = float(starting_inventory_ton)
    begin_inv_at_arrival = float(starting_inventory_ton)

    # arrival 이전은 기존 세계 그대로 진행
    for month_idx in range(1, arrival_month_idx):
        available = begin_inv + float(open_po_path[month_idx - 1])
        ending_inv = max(0.0, available - float(usage_path[month_idx - 1]))
        begin_inv = ending_inv
    begin_inv_at_arrival = begin_inv

    cum_receipts = 0.0
    cum_demand = 0.0
    required_qty = 0.0
    max_gap_qty = 0.0

    for month_idx in range(arrival_month_idx, len(usage_path) + 1):
        cum_receipts += float(open_po_path[month_idx - 1])
        cum_demand += float(usage_path[month_idx - 1])
        gap_without_candidate = cum_demand - (begin_inv_at_arrival + cum_receipts)
        max_gap_qty = max(max_gap_qty, gap_without_candidate)
        required_qty = max(required_qty, gap_without_candidate + safety_stock_ton)

    required_first_shortage_qty = max(0.0, float(usage_path[arrival_month_idx - 1]) - (begin_inv_at_arrival + float(open_po_path[arrival_month_idx - 1])) + safety_stock_ton)



    return {
        "begin_inv_at_arrival_ton": float(begin_inv_at_arrival),
        "max_cum_gap_arrival_ton": float(max(0.0, max_gap_qty)),
        "required_buy_qty_arrival_ton": float(max(0.0, required_qty)),
        "required_buy_qty_first_shortage_ton": float(max(0.0, required_first_shortage_qty)),
    }

# 블록 7) no-buy baseline helper 계산 본체
# “지금 새로 안 산다고 가정했을 때, 앞으로 shortage가 나는지, 언제 처음 나는지, 전체 shortage가 얼마나 되는지, 
# 비용 상방이 얼마나 있는지, 그래서 A-risk/B-risk를 켜야 하는지를 한 번에 계산한다.”
def _simulate_no_buy_helpers(
    starting_inventory_ton: float,
    usage_path: List[float],
    open_po_path: List[float],
    expected_cost_path: List[float],
    now_cost: float,
    freight_current: float,
    usd_current: float,
    cfg: PipelineConfig,
) -> Dict[str, float]:
    """합의된 baseline world(no new buy, existing open PO only)에서 helper 계산.

    수정 포인트
    -----------
    - ending inventory를 max(0)로 clip한 값만 보면 a_min_end_inv_ton이 0에 붙어버린다.
    - 그래서 raw ending inventory와 clipped ending inventory를 분리한다.
    - shortage anchored candidate를 위해 arrival 시점 기준 required buy qty도 같이 계산한다.
    """
    begin_inv = float(starting_inventory_ton)
    end_inv_raw_path: List[float] = []
    end_inv_clipped_path: List[float] = []
    shortage_path: List[float] = []

    for demand_ton, open_po_ton in zip(usage_path, open_po_path):
        available = begin_inv + float(open_po_ton)
        raw_end_inv = available - float(demand_ton)
        shortage = max(0.0, -raw_end_inv)
        ending_inv = max(0.0, raw_end_inv)

        end_inv_raw_path.append(float(raw_end_inv))
        end_inv_clipped_path.append(float(ending_inv))
        shortage_path.append(float(shortage))
        begin_inv = ending_inv

    cover_months = [
        (end_inv / usage if usage > 0 else np.nan)
        for end_inv, usage in zip(end_inv_clipped_path, usage_path)
    ]

    shortage_months = [i + 1 for i, qty in enumerate(shortage_path) if qty > 0]
    a_first_shortage_month_idx = float(shortage_months[0]) if shortage_months else np.nan
    arrival_month_idx = int(a_first_shortage_month_idx) if shortage_months else cfg.lt_months
    arrival_month_idx = max(1, min(arrival_month_idx, cfg.horizon_months))

    req = _calc_required_buy_qty_from_arrival(
        starting_inventory_ton=starting_inventory_ton,
        usage_path=usage_path,
        open_po_path=open_po_path,
        arrival_month_idx=arrival_month_idx,
        safety_stock_ton=cfg.safety_stock_ton,
    )

    a_min_end_inv_ton = float(np.min(end_inv_raw_path))
    a_min_cover_months = float(np.nanmin(cover_months))
    a_emergency_buy_needed_flag = int(any(qty > 0 for qty in shortage_path))
    baseline_total_shortage_ton = float(np.sum(shortage_path))

    cost_vs_now = [
        (float(cost) / float(now_cost) - 1.0) if now_cost > 0 else 0.0
        for cost in expected_cost_path
    ]
    peak_cost_vs_now_pct = float(np.max(cost_vs_now))
    high_cost_month_count = int(sum(1 for v in cost_vs_now if v >= 0.05))

    if shortage_months:
        shortage_month_costs = [cost_vs_now[idx - 1] for idx in shortage_months]
        forced_buy_cost_vs_now_pct = float(np.max(shortage_month_costs))
    else:
        forced_buy_cost_vs_now_pct = float(max(0.0, peak_cost_vs_now_pct))

    b_forced_buy_flag = int(a_emergency_buy_needed_flag == 1)

    freight_stress = np.clip((freight_current - 95.0) / 30.0, 0.0, 1.0)
    fx_stress = np.clip((usd_current - 1260.0) / 80.0, 0.0, 1.0)
    shortage_severity = np.clip(max(shortage_path) / cfg.monthly_usage_base_ton, 0.0, 1.0)
    premium_score = 100.0 * np.clip(
        0.45 * max(0.0, peak_cost_vs_now_pct / 0.10)
        + 0.25 * freight_stress
        + 0.15 * fx_stress
        + 0.15 * shortage_severity,
        0.0,
        1.0,
    )

    target_a = int(
        (a_emergency_buy_needed_flag == 1)
        or (a_min_end_inv_ton < 0)
        or (a_min_cover_months < 0.35)
        or (
            (a_min_cover_months < 0.50)
            and (not pd.isna(a_first_shortage_month_idx))
            and (a_first_shortage_month_idx <= 2)
        )
    )

    target_b = int(
        ((b_forced_buy_flag == 1) and (forced_buy_cost_vs_now_pct >= 0.05))
        or (premium_score >= 60.0)
        or (peak_cost_vs_now_pct >= 0.08)
        or ((peak_cost_vs_now_pct >= 0.05) and (high_cost_month_count >= 2))
    )

    return {
        "a_min_end_inv_ton": float(a_min_end_inv_ton),
        "a_min_cover_months": float(a_min_cover_months),
        "a_emergency_buy_needed_flag": int(a_emergency_buy_needed_flag),
        "a_first_shortage_month_idx": a_first_shortage_month_idx,
        "baseline_total_shortage_ton": baseline_total_shortage_ton,
        **req,
        "b_peak_cost_vs_now_pct": float(peak_cost_vs_now_pct),
        "b_forced_buy_flag": int(b_forced_buy_flag),
        "b_forced_buy_cost_vs_now_pct": float(forced_buy_cost_vs_now_pct),
        "b_emergency_premium_score": float(premium_score),
        "b_high_cost_month_count": int(high_cost_month_count),
        "target_a_rule": int(target_a),
        "target_b_rule": int(target_b),
    }

# 블록 8) build_hybrid_decision_master_df 시작: 외생 리턴 특성 만들기
# “시장 레벨만 보지 말고 최근 1개월/3개월 추세도 같이 의사결정 입력으로 넣자.”
def build_hybrid_decision_master_df(
    exogenous_df: pd.DataFrame,
    cfg: PipelineConfig,
    seed: int = 42,
    keep_latest_only: bool = False,
) -> pd.DataFrame:
    """외생 시계열 + synthetic 운영규칙으로 decision master 생성.

    입력:
        exogenous_df: as_of_month / global_raw_sugar_price / usdkrw / freight_index
    출력:
        final decision layer가 바로 먹을 수 있는 decision_master_df
    """
    exog = _ensure_monthly_exogenous_df(exogenous_df)
    rng = np.random.default_rng(seed)

    # return / rolling feature
    exog["sugar_ret_1m"] = exog["global_raw_sugar_price"].pct_change()
    exog["usdkrw_ret_1m"] = exog["usdkrw"].pct_change()
    exog["freight_ret_1m"] = exog["freight_index"].pct_change()
    exog["sugar_ret_3m"] = exog["global_raw_sugar_price"].pct_change(3)
    exog["usdkrw_ret_3m"] = exog["usdkrw"].pct_change(3)
    exog["freight_ret_3m"] = exog["freight_index"].pct_change(3)

# 블록 9) decision row 생성 루프
# “2023-04, 2023-05, 2023-06 … 각 기준월마다 하나의 구매 판단 장면을 만든다. 
# 그 장면에는 현재 상태와 향후 몇 개월 경로가 같이 붙는다.”
    rows: List[Dict] = []
    max_idx = len(exog) - cfg.horizon_months - 1
    if max_idx < 0:
        raise ValueError("Need at least horizon_months + 1 rows in exogenous_df.")

    for i in range(3, len(exog) - cfg.horizon_months):
        cur = exog.iloc[i]
        fut = exog.iloc[i + 1: i + 1 + cfg.horizon_months].copy().reset_index(drop=True)

        now_cost = _calc_landed_cost_proxy(
            sugar=float(cur["global_raw_sugar_price"]),
            usdkrw=float(cur["usdkrw"]),
            freight=float(cur["freight_index"]),
        )

        future_cost_path = [
            _calc_landed_cost_proxy(
                sugar=float(r["global_raw_sugar_price"]),
                usdkrw=float(r["usdkrw"]),
                freight=float(r["freight_index"]),
            )
            for _, r in fut.iterrows()
        ]

  # 블록 10) synthetic 재고 상태 생성
  # “재고는 장부상 재고만 있으면 안 되고, 실제로 쓸 수 있는 usable inventory로 정리해야 한다.”
        # synthetic inventory state
        seasonality_now = _month_seasonality(pd.Timestamp(cur["as_of_month"]).month)
        blocked_inventory_ton = float(max(0.0, 400 + rng.normal(600, 250)))
        on_hand_inventory_ton = float(
            18_000
            + 6_000 * (1.0 / max(0.65, seasonality_now))
            + rng.normal(0.0, 2500.0)
            - 2000.0 * int(cur.get("shock_event_flag", 0))
        )
        on_hand_inventory_ton = float(np.clip(on_hand_inventory_ton, 8_000.0, 42_000.0))
        usable_inventory_ton = float(max(0.0, on_hand_inventory_ton - blocked_inventory_ton))

# 블록 11) usage/open PO/helper/working capital 생성
# “현재 장면의 수요경로, 예정입고경로, no-buy 기준 부족/비용 위험, 
# 자금압박까지 한 번에 만들어서 decision row에 붙인다.”
        usage_path = _make_usage_path(
            future_months=fut["as_of_month"],
            future_shock_flags=fut.get("shock_event_flag", pd.Series([0] * len(fut))),
            cfg=cfg,
            rng=rng,
        )
        open_po_path = _make_open_po_path(
            current_inventory_ton=usable_inventory_ton,
            current_month_idx=i,
            cfg=cfg,
        )

        helpers = _simulate_no_buy_helpers(
            starting_inventory_ton=usable_inventory_ton,
            usage_path=usage_path,
            open_po_path=open_po_path,
            expected_cost_path=future_cost_path,
            now_cost=now_cost,
            freight_current=float(cur["freight_index"]),
            usd_current=float(cur["usdkrw"]),
            cfg=cfg,
        )

        working_capital_pressure_score = float(
            np.clip(
                45
                + 180 * max(0.0, helpers["b_peak_cost_vs_now_pct"])
                + 25 * max(0.0, (usable_inventory_ton / cfg.monthly_usage_base_ton) - 0.7)
                + rng.normal(0.0, 5.0),
                15.0,
                98.0,
            )
        )
# 블록 12) 최종 row 딕셔너리 만들기
# “지금 이 월의 구매 판단 장면을 하나의 decision row로 만든다. 여기엔 현재 재고, 현재 원가, 외생 추세, 자금압박, 
# MOQ/lot/cap, helper, A/B rule이 다 같이 들어간다.”
        row: Dict[str, object] = {
            "decision_id": f"{cfg.material_code}_{pd.Timestamp(cur['as_of_month']).strftime('%Y-%m')}",
            "decision_month": pd.Timestamp(cur["as_of_month"]).strftime("%Y-%m"),
            "material_code": cfg.material_code,
            "as_of_month": pd.Timestamp(cur["as_of_month"]),
            "decision_date": (pd.Timestamp(cur["as_of_month"]) + pd.offsets.MonthEnd(0)).normalize(),
            "lt_months": cfg.lt_months,
            "candidate_arrival_month_idx": int(min(cfg.horizon_months, max(1, helpers.get("a_first_shortage_month_idx") if pd.notna(helpers.get("a_first_shortage_month_idx")) else cfg.lt_months))),
            "on_hand_inventory_ton": on_hand_inventory_ton,
            "blocked_inventory_ton": blocked_inventory_ton,
            "usable_inventory_ton": usable_inventory_ton,
            "current_inventory_ton": usable_inventory_ton,  # final decision layer 시작 inventory는 usable 기준으로 통일
            "now_landed_cost_per_ton": float(now_cost),
            "global_raw_sugar_price": float(cur["global_raw_sugar_price"]),
            "usdkrw": float(cur["usdkrw"]),
            "freight_index": float(cur["freight_index"]),
            "sugar_ret_1m": float(cur["sugar_ret_1m"]) if pd.notna(cur["sugar_ret_1m"]) else 0.0,
            "usdkrw_ret_1m": float(cur["usdkrw_ret_1m"]) if pd.notna(cur["usdkrw_ret_1m"]) else 0.0,
            "freight_ret_1m": float(cur["freight_ret_1m"]) if pd.notna(cur["freight_ret_1m"]) else 0.0,
            "sugar_ret_3m": float(cur["sugar_ret_3m"]) if pd.notna(cur["sugar_ret_3m"]) else 0.0,
            "usdkrw_ret_3m": float(cur["usdkrw_ret_3m"]) if pd.notna(cur["usdkrw_ret_3m"]) else 0.0,
            "freight_ret_3m": float(cur["freight_ret_3m"]) if pd.notna(cur["freight_ret_3m"]) else 0.0,
            "working_capital_pressure_score": working_capital_pressure_score,
            "moq_ton": cfg.moq_ton,
            "lot_multiple_ton": cfg.lot_multiple_ton,
            "warehouse_capacity_ton": cfg.warehouse_capacity_ton,
            "shock_event_flag_now": int(cur.get("shock_event_flag", 0)),
            **helpers,
        }

# 블록 13) horizon wide 컬럼 붙이기
# “앞으로 1개월차, 2개월차, 3개월차… 사용량/예정입고/예상원가를 컬럼으로 펼쳐서 붙인다.”
        for month_idx in range(1, cfg.horizon_months + 1):
            row[f"usage_m{month_idx}_ton"] = float(usage_path[month_idx - 1])
            row[f"open_po_m{month_idx}_ton"] = float(open_po_path[month_idx - 1])
            row[f"expected_landed_cost_m{month_idx}_per_ton"] = float(future_cost_path[month_idx - 1])

        rows.append(row)

# 블록 14) 최종 DataFrame 생성 + latest only 옵션
# “학습용 historical decision panel로도 쓸 수 있고, 현재월 1건 의사결정 row만 뽑아 live 판단용으로도 쓸 수 있게 만든다.”
    decision_master_df = pd.DataFrame(rows).sort_values("as_of_month").reset_index(drop=True)

    if keep_latest_only:
        decision_master_df = decision_master_df.iloc[[-1]].reset_index(drop=True)

    return decision_master_df
