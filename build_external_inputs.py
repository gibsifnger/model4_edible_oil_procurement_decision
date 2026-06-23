from __future__ import annotations

"""External 3-factor monthly input builder for model2.

Purpose
-------
Create a monthly CSV with the exact columns expected by the current decision generator:
- as_of_month
- global_raw_sugar_price
- usdkrw
- freight_index

Design choice
-------------
1) Sugar: fetched from official FRED IMF-linked monthly series.
2) USDKRW: fetched from BOK ECOS Open API if credentials/codes are provided.
3) Freight index: because an unauthenticated official historical CSV/API for FBX was not
   confirmed in the retrieved official sources, this script supports:
   - local/manual CSV input (recommended for realism), or
   - synthetic fallback (recommended for immediate execution).

This keeps the monthly time axis aligned with the current model2 generator.
"""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

# 2. FRED 원당 가격 URL
FRED_SUGAR_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PSUGAISAUSDM"

# 3. ExternalBuildConfig — 외생변수 생성 설정 묶음
# 이건 시장자료 생성 요청서다.

# 구매팀 표현	코드 설정
# "2019년부터 최근까지 봐줘"	start_month, end_month
# "환율은 ECOS에서 가져와"	ecos_api_key, ecos_stat_code, ecos_item_code_*
# "운임은 임시값으로라도 만들어줘"	freight_mode="synthetic"
# "운임은 내가 만든 CSV를 써줘"	freight_mode="csv", freight_csv_path
@dataclass
class ExternalBuildConfig:
    start_month: str = "2019-01-01"
    end_month: Optional[str] = None
    output_csv: str = "external_inputs_monthly.csv"

    # ECOS settings (must be supplied by user or env vars for real BOK pull)
    ecos_api_key: Optional[str] = None
    ecos_stat_code: Optional[str] = None
    ecos_cycle: str = "M"
    ecos_item_code_1: Optional[str] = None
    ecos_item_code_2: Optional[str] = None
    ecos_item_code_3: Optional[str] = None

    # Freight settings
    freight_mode: str = "synthetic"  # synthetic | csv
    freight_csv_path: Optional[str] = None
    freight_date_col: str = "Date"
    freight_value_col: str = "freight_index"
    seed: int = 42


# =========================================================
# Common helpers
# =========================================================
# 4. _to_month_start() — 날짜를 월초로 맞추는 함수
def _to_month_start(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.to_period("M").dt.to_timestamp()


# =========================================================
# 1) Sugar from FRED
# =========================================================
# 5. fetch_fred_sugar_monthly() — FRED에서 원당 가격 가져오기
def fetch_fred_sugar_monthly() -> pd.DataFrame:
    """Official FRED CSV pull for PSUGAISAUSDM.

    Output columns:
    - as_of_month
    - global_raw_sugar_price
    """
    resp = requests.get(FRED_SUGAR_CSV_URL, timeout=30)
    resp.raise_for_status()

    df = pd.read_csv(pd.io.common.StringIO(resp.text))
    df.columns = [c.strip() for c in df.columns]

    if "DATE" not in df.columns:
        raise ValueError("Unexpected FRED sugar response: DATE column missing")

    value_col = [c for c in df.columns if c != "DATE"][0]
    df = df.rename(columns={"DATE": "as_of_month", value_col: "global_raw_sugar_price"})
    df["as_of_month"] = _to_month_start(df["as_of_month"])
    df["global_raw_sugar_price"] = pd.to_numeric(df["global_raw_sugar_price"], errors="coerce")
    df = df.dropna(subset=["global_raw_sugar_price"]).sort_values("as_of_month").reset_index(drop=True)
    return df


# =========================================================
# 2) USDKRW from ECOS
# =========================================================
# 6. build_ecos_url() — ECOS API 주소 만들기
def build_ecos_url(
    api_key: str,
    stat_code: str,
    cycle: str,
    start_month: str,
    end_month: str,
    item_code_1: Optional[str] = None,
    item_code_2: Optional[str] = None,
    item_code_3: Optional[str] = None,
) -> str:
    """Build ECOS StatisticSearch URL.

    Notes
    -----
    ECOS needs an API key and the exact statistic/item codes from the official code search.
    This function keeps those codes configurable rather than hard-coding uncertain values.
    """
    start_fmt = pd.Timestamp(start_month).strftime("%Y%m")
    end_fmt = pd.Timestamp(end_month).strftime("%Y%m")

    parts = [
        "https://ecos.bok.or.kr/api",
        api_key,
        "json",
        "StatisticSearch",
        "1",
        "10000",
        stat_code,
        cycle,
        start_fmt,
        end_fmt,
    ]
    for code in [item_code_1, item_code_2, item_code_3]:
        if code:
            parts.append(code)

    return "/".join(parts)


# 7. fetch_ecos_usdkrw_monthly() — ECOS에서 환율 가져오기
def fetch_ecos_usdkrw_monthly(
    api_key: str,
    stat_code: str,
    start_month: str,
    end_month: str,
    cycle: str = "M",
    item_code_1: Optional[str] = None,
    item_code_2: Optional[str] = None,
    item_code_3: Optional[str] = None,
) -> pd.DataFrame:
    """Pull monthly USDKRW from BOK ECOS.

    Expected result JSON shape:
      StatisticSearch -> row -> TIME, DATA_VALUE

    Output columns:
    - as_of_month
    - usdkrw
    """
    url = build_ecos_url(
        api_key=api_key,
        stat_code=stat_code,
        cycle=cycle,
        start_month=start_month,
        end_month=end_month,
        item_code_1=item_code_1,
        item_code_2=item_code_2,
        item_code_3=item_code_3,
    )

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    rows = payload.get("StatisticSearch", {}).get("row", [])
    if not rows:
        raise ValueError(
            "ECOS response has no rows. Check API key and statistic/item codes. "
            f"URL used: {url}"
        )

    df = pd.DataFrame(rows)
    if "TIME" not in df.columns or "DATA_VALUE" not in df.columns:
        raise ValueError("Unexpected ECOS response: TIME / DATA_VALUE missing")

    # Monthly cycle M => YYYYMM
    df["as_of_month"] = pd.to_datetime(df["TIME"].astype(str), format="%Y%m").dt.to_period("M").dt.to_timestamp()
    df["usdkrw"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
    df = df[["as_of_month", "usdkrw"]].dropna().sort_values("as_of_month").reset_index(drop=True)
    return df


# =========================================================
# 3) Freight index
# =========================================================
# 8. load_freight_index_from_csv() — 운임 CSV를 월별 운임지수로 변환
def load_freight_index_from_csv(
    csv_path: str,
    date_col: str = "Date",
    value_col: str = "freight_index",
) -> pd.DataFrame:
    """Load a local/manual freight CSV and align it to monthly series.

    The file may be daily/weekly/monthly. It will be converted to monthly mean.
    """
    df = pd.read_csv(csv_path)
    if date_col not in df.columns or value_col not in df.columns:
        raise ValueError(f"Freight CSV must contain [{date_col}, {value_col}] columns")

    df = df[[date_col, value_col]].copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=[date_col, value_col])

    out = (
        df.assign(as_of_month=_to_month_start(df[date_col]))
          .groupby("as_of_month", as_index=False)[value_col]
          .mean()
          .rename(columns={value_col: "freight_index"})
          .sort_values("as_of_month")
          .reset_index(drop=True)
    )
    return out


# 9. build_synthetic_freight_from_sugar_fx() — 합성 운임 생성
def build_synthetic_freight_from_sugar_fx(
    base_df: pd.DataFrame,
    seed: int = 42,
) -> pd.DataFrame:
    """Immediate-run fallback when no official/public freight CSV is supplied.

    Logic:
    - keep the monthly axis identical to the merged external frame
    - make freight partly co-move with sugar/fx plus smooth noise
    - scale to index-like level
    """
    rng = np.random.default_rng(seed)
    df = base_df[["as_of_month", "global_raw_sugar_price", "usdkrw"]].copy().sort_values("as_of_month")

    sugar_z = (df["global_raw_sugar_price"] - df["global_raw_sugar_price"].mean()) / max(df["global_raw_sugar_price"].std(ddof=0), 1e-9)
    fx_z = (df["usdkrw"] - df["usdkrw"].mean()) / max(df["usdkrw"].std(ddof=0), 1e-9)

    noise = rng.normal(0, 1, len(df))
    smooth_noise = pd.Series(noise).rolling(3, min_periods=1).mean().values

    freight = 100 + 7.5 * sugar_z + 5.0 * fx_z + 2.5 * smooth_noise
    out = pd.DataFrame({
        "as_of_month": df["as_of_month"],
        "freight_index": np.round(np.clip(freight, 70, 180), 3),
    })
    return out


# =========================================================
# 4) Final builder
# =========================================================
# 10. build_external_inputs_monthly() — 최종 외생변수 월별 표 만들기
def build_external_inputs_monthly(cfg: ExternalBuildConfig) -> pd.DataFrame:
    if cfg.end_month is None:
        cfg.end_month = pd.Timestamp.today().to_period("M").to_timestamp().strftime("%Y-%m-%d")

    # sugar: official FRED
    sugar_df = fetch_fred_sugar_monthly()
    sugar_df = sugar_df[
        (sugar_df["as_of_month"] >= pd.Timestamp(cfg.start_month).to_period("M").to_timestamp())
        & (sugar_df["as_of_month"] <= pd.Timestamp(cfg.end_month).to_period("M").to_timestamp())
    ].copy()

    # fx: ECOS or fail explicitly
    if not cfg.ecos_api_key or not cfg.ecos_stat_code:
        raise ValueError(
            "ECOS API key / stat code are required for real usdkrw pull. "
            "Pass --ecos-api-key and --ecos-stat-code (and item codes if needed)."
        )

    fx_df = fetch_ecos_usdkrw_monthly(
        api_key=cfg.ecos_api_key,
        stat_code=cfg.ecos_stat_code,
        start_month=cfg.start_month,
        end_month=cfg.end_month,
        cycle=cfg.ecos_cycle,
        item_code_1=cfg.ecos_item_code_1,
        item_code_2=cfg.ecos_item_code_2,
        item_code_3=cfg.ecos_item_code_3,
    )

    merged = sugar_df.merge(fx_df, on="as_of_month", how="inner").sort_values("as_of_month").reset_index(drop=True)
    if merged.empty:
        raise ValueError("Merged sugar/fx dataframe is empty. Check month range and ECOS series.")
# 11. 운임 붙이기 — CSV 방식
    if cfg.freight_mode == "csv":
        if not cfg.freight_csv_path:
            raise ValueError("freight_mode=csv requires --freight-csv-path")
        freight_df = load_freight_index_from_csv(
            csv_path=cfg.freight_csv_path,
            date_col=cfg.freight_date_col,
            value_col=cfg.freight_value_col,
        )
        merged = merged.merge(freight_df, on="as_of_month", how="left")
        merged["freight_index"] = merged["freight_index"].ffill().bfill()
 # 12. 운임 붙이기 — synthetic 방식
    elif cfg.freight_mode == "synthetic":
        freight_df = build_synthetic_freight_from_sugar_fx(merged, seed=cfg.seed)
        merged = merged.merge(freight_df, on="as_of_month", how="left")
    else:
        raise ValueError("freight_mode must be one of: csv, synthetic")
# 13. 최종 컬럼 정리 후 반환
    merged = merged[["as_of_month", "global_raw_sugar_price", "usdkrw", "freight_index"]].copy()
    merged = merged.sort_values("as_of_month").reset_index(drop=True)
    return merged


# =========================================================
# 5) CLI
# =========================================================
# 14. 이 파일 자체 실행용 parse_args()
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build external 3-factor monthly CSV for model2")
    parser.add_argument("--start-month", default="2019-01-01")
    parser.add_argument("--end-month", default=None)
    parser.add_argument("--output-csv", default="external_inputs_monthly.csv")

    parser.add_argument("--ecos-api-key", default=os.getenv("ECOS_API_KEY"))
    parser.add_argument("--ecos-stat-code", default=os.getenv("ECOS_STAT_CODE"))
    parser.add_argument("--ecos-cycle", default=os.getenv("ECOS_CYCLE", "M"))
    parser.add_argument("--ecos-item-code-1", default=os.getenv("ECOS_ITEM_CODE_1"))
    parser.add_argument("--ecos-item-code-2", default=os.getenv("ECOS_ITEM_CODE_2"))
    parser.add_argument("--ecos-item-code-3", default=os.getenv("ECOS_ITEM_CODE_3"))

    parser.add_argument("--freight-mode", choices=["synthetic", "csv"], default="synthetic")
    parser.add_argument("--freight-csv-path", default=None)
    parser.add_argument("--freight-date-col", default="Date")
    parser.add_argument("--freight-value-col", default="freight_index")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()

# 15. __main__ — 이 파일을 직접 실행했을 때
if __name__ == "__main__":
    args = parse_args()
    cfg = ExternalBuildConfig(
        start_month=args.start_month,
        end_month=args.end_month,
        output_csv=args.output_csv,
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

    out = build_external_inputs_monthly(cfg)
    out_path = Path(args.output_csv)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"saved: {out_path.resolve()}")
    print(out.tail(12).to_string(index=False))
