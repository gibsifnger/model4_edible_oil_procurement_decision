
# 외생 시계열과 synthetic 운영규칙을 이용해 **decision_master_df**를 만든다
# 입력	   exogenous_df, cfg, seed, keep_latest_only
# 출력	   decision_master_df
# 다음 파일로 넘기는 것	baseline_flow_builder.py, candidate_policy.py 등이 먹는 decision row 원본
# 여기서 봐야 하는 중심축	“외부시장 데이터 + 운영가정 + 무매입 기준 helper”를 합쳐 판단 시작점 row를 만든다

from __future__ import annotations

from typing import Dict, List
import numpy as np
import pandas as pd

from ..config import PipelineConfig
#A가 완전 순수층은 아니고 B 일부를 호출함
from ..B_interpret.helper_calculator import _simulate_no_buy_helpers

# 원당가/환율/운임 월별 표가 제대로 안 들어오면 아예 판단을 시작하지 않겠다
def _ensure_monthly_exogenous_df(exogenous_df: pd.DataFrame) -> pd.DataFrame:
    required = ["as_of_month", "global_raw_sugar_price", "usdkrw", "freight_index"]
    missing = [c for c in required if c not in exogenous_df.columns]
    if missing:
        raise ValueError(f"exogenous_df missing required columns: {missing}")

    out = exogenous_df.copy()
    out["as_of_month"] = pd.to_datetime(out["as_of_month"]).dt.to_period("M").dt.to_timestamp()
    out = out.sort_values("as_of_month").drop_duplicates("as_of_month", keep="last").reset_index(drop=True)
    return out

# “현실 데이터를 못 받았어도, 최소한 시장이 출렁이는 환경을 가정해서 파이프라인은 끝까지 돌려보자.”
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

# “시장지표를 그대로 보지 말고, 구매의사결정에 쓸 현재/미래 landed cost 감각으로 변환하자.”
# 원당가, 환율, 운임을 가중합해서 간이 landed cost proxy를 만든다. 
# 식은 0.72 * sugar + 0.18 * usdkrw + 1.10 * freight
def _calc_landed_cost_proxy(sugar: float, usdkrw: float, freight: float) -> float:
    return float(0.72 * sugar + 0.18 * usdkrw + 1.10 * freight)


# 미래 사용량을 단순 상수로 두지 않고, 월별 계절성 + 행사성 + BOM성 요인으로 흔들리게 만든다.
   # shock_flag가 있는 달은 promo에 약한 음수 offset도 준다.
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

# “앞으로 몇 달간 얼마씩 쓸 것 같은지를 먼저 대략 정해놓은 기본 예상표를 만들자.”
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

# “지금 새 발주를 안 해도, 기존 PO 기준으로 앞으로 들어올 물량은 이 정도라고 놓자.”
  # 재고가 빡빡하면 첫 달은 MOQ, 아니면 lot multiple, 
  # 둘째 달은 MOQ, 셋째 달은 짝수 월이면 lot multiple, 이후는 0이다.
def _make_open_po_path(
    current_inventory_ton: float,
    current_month_idx: int,
    cfg: PipelineConfig,
) -> List[float]:
    """기존 open PO / 예정입고 skeleton."""
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


#“이번 달에 원당을 새로 사야 할지 보려면, 현재 원가/재고/차단재고/예정입고/앞으로 몇 달 사용량/자금압박부터 한 row에 정리.”
# 외생 시계열과 synthetic 운영규칙을 결합해, 한 달 단위 구매판단의 시작 row(decision_master_df)를 만든다.
def build_hybrid_decision_master_df(
    exogenous_df: pd.DataFrame,
    cfg: PipelineConfig,
    seed: int = 42,
    keep_latest_only: bool = False,
) -> pd.DataFrame:
    """외생 시계열 + synthetic 운영규칙으로 decision master 생성.
    A 역할:
    - 외생 정리
    - synthetic 운영상태 생성
    - horizon usage/open PO/cost path 구성
    B 호출:
    - helper / rule / shortage anchored 기준값은 B_interpret.helper_calculator 에 위임
    """
    exog = _ensure_monthly_exogenous_df(exogenous_df)
    rng = np.random.default_rng(seed)

    exog["sugar_ret_1m"] = exog["global_raw_sugar_price"].pct_change()
    exog["usdkrw_ret_1m"] = exog["usdkrw"].pct_change()
    exog["freight_ret_1m"] = exog["freight_index"].pct_change()
    exog["sugar_ret_3m"] = exog["global_raw_sugar_price"].pct_change(3)
    exog["usdkrw_ret_3m"] = exog["usdkrw"].pct_change(3)
    exog["freight_ret_3m"] = exog["freight_index"].pct_change(3)

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
         # ③ B helper를 이미 여기서 호출한다
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
            "current_inventory_ton": usable_inventory_ton,
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

        for month_idx in range(1, cfg.horizon_months + 1):
            row[f"usage_m{month_idx}_ton"] = float(usage_path[month_idx - 1])
            row[f"open_po_m{month_idx}_ton"] = float(open_po_path[month_idx - 1])
            row[f"expected_landed_cost_m{month_idx}_per_ton"] = float(future_cost_path[month_idx - 1])

        rows.append(row)

    decision_master_df = pd.DataFrame(rows).sort_values("as_of_month").reset_index(drop=True)

    if keep_latest_only:
        decision_master_df = decision_master_df.iloc[[-1]].reset_index(drop=True)

    return decision_master_df


__all__ = [
    "_ensure_monthly_exogenous_df",
    "make_demo_exogenous_df",
    "_calc_landed_cost_proxy",
    "_month_seasonality",
    "_promo_factor",
    "_bom_factor",
    "_make_usage_path",
    "_make_open_po_path",
    "build_hybrid_decision_master_df",
]
