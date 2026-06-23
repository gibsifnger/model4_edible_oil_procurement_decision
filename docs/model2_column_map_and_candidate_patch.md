# model2 생성기별 컬럼 맵 + 후보군 확장 패치

## 1) 생성기별 컬럼 맵

| 파일 | 입력 핵심 컬럼 | 새로 만드는 핵심 컬럼 | 다음 단계로 넘기는 판단 포인트 |
|---|---|---|---|
| `run_all_in_one_hgb_pipeline.py` | CLI 인자, 외생 CSV/build/demo, artifact 경로 | `meta_df`, `exogenous_df`, `historical_master_df`, `scored_latest_df`, 최종 outputs dict | 전체 파이프라인 실행/저장 |
| `decision_generator.py` | `as_of_month`, `global_raw_sugar_price`, `usdkrw`, `freight_index` + 운영 가정 | inventory 계열, `usage_m1~m4`, `open_po_m1~m4`, `expected_landed_cost_m1~m4`, helper, target rule | 현재 row의 shortage 구조와 required qty |
| `baseline_flow.py` | current inventory, usage/open PO horizon | baseline no-buy 월별 흐름 | no-buy shortage 기준선 |
| `candidates.py` | `moq_ton`, `lot_multiple_ton`, `required_buy_qty_arrival_ton`, `max_cum_gap_arrival_ton`, `baseline_total_shortage_ton`, `required_buy_qty_first_shortage_ton` | `candidate_name`, `candidate_qty_ton`, `candidate_po_value_now` | 비교할 후보 세트 |
| `gates.py` | candidate_df + warehouse / WC / first shortage month | `candidate_status`, `hard_fail_reason`, `soft_warning_reason`, projected inventory/shortage | 실행가능/조건부/불가 판정 |
| `scenarios.py` / `simulation.py` | gated candidate + demand/cost/delay scenario | candidate x scenario x month 결과 | scenario별 shortage/cost 비교 |
| `compare_select.py` | scenario summary / robust summary + final pred | `scenario_summary_df`, `robust_summary_df`, `best_candidate_df` | robust 여부 / worst shortage / 비용 기준 후보 선택 |
| `final_action.py` | selected candidate + need_buy_flag + gate 결과 | `final_action`, `final_reason`, `additional_check_reason` | 선매입 검토 / 관망 / 추가확인 |

## 2) 최소 수정 방향

핵심은 `candidates.py`만 수정해서 `MOQ ~ shortage_anchored` 사이 ladder 후보를 추가하는 것입니다.

이렇게 하면:
- `gates.py`는 `candidate_df`를 행 단위로 순회하므로 그대로 동작
- `simulation.py` / `compare_select.py`도 candidate_name, qty 기준으로 generic하게 돌기 때문에 그대로 동작
- 즉 후보 생성 해상도만 올리고 나머지 파이프라인은 유지할 수 있음

## 3) 교체용 `model2_pipeline/candidates.py`

```python
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .config import PipelineConfig


def round_up_to_lot(qty_ton: float, lot_multiple_ton: float) -> float:
    if qty_ton <= 0:
        return 0.0
    return float(np.ceil(qty_ton / lot_multiple_ton) * lot_multiple_ton)


def normalize_nonzero_candidate(qty_ton: float, moq_ton: float, lot_multiple_ton: float) -> float:
    if qty_ton <= 0:
        return 0.0
    return round_up_to_lot(max(qty_ton, moq_ton), lot_multiple_ton)


def derive_shortage_anchored_qty(row: pd.Series, cfg: PipelineConfig) -> float:
    """부족 규모에 비례한 required candidate를 만든다.

    수정 원칙
    ---------
    - baseline helper가 계산한 `required_buy_qty_arrival_ton`을 1순위로 쓴다.
    - 없으면 `max_cum_gap_arrival_ton + safety_stock`을 fallback으로 쓴다.
    - 이것도 없으면 총 shortage와 first-shortage relief를 보조적으로 쓴다.
    - 여기서 일부러 capacity를 미리 자르지 않는다.
      이유: "필요수량"과 "실행가능수량"은 다른 층이다. 실행가능성은 gate가 자른다.
    """
    required_qty = float(row.get("required_buy_qty_arrival_ton", np.nan))
    max_gap_qty = float(row.get("max_cum_gap_arrival_ton", np.nan))
    total_shortage = float(row.get("baseline_total_shortage_ton", np.nan))
    first_shortage_relief = float(row.get("required_buy_qty_first_shortage_ton", np.nan))

    candidates = []
    if pd.notna(required_qty):
        candidates.append(required_qty)
    if pd.notna(max_gap_qty):
        candidates.append(max_gap_qty + cfg.safety_stock_ton)
    if pd.notna(first_shortage_relief):
        candidates.append(first_shortage_relief)
    if pd.notna(total_shortage):
        candidates.append(total_shortage * 0.85)

    raw_qty = max([0.0, *candidates])
    return normalize_nonzero_candidate(raw_qty, cfg.moq_ton, cfg.lot_multiple_ton)


def build_ladder_candidates(
    shortage_anchored_qty: float,
    moq_ton: float,
    lot_multiple_ton: float,
) -> List[tuple[str, float]]:
    """MOQ와 shortage anchored 사이의 중간 후보군을 추가한다.

    예시 (moq=5000, lot=2500, shortage_anchored=55000)
    - MOQ+1lot = 7500
    - MOQ+2lot = 10000
    - ...
    - MOQ+14lot = 40000
    - shortage_anchored = 55000

    목적:
    - 7,500톤과 55,000톤 사이가 비는 문제를 줄인다.
    - 필요한 물량과 실행 가능한 물량 사이의 더 많은 trade-off를 보게 한다.
    """
    specs: List[tuple[str, float]] = [("MOQ", moq_ton)]

    # 중간 ladder 후보: MOQ+1lot ~ MOQ+14lot
    for n_lot in range(1, 15):
        qty = moq_ton + n_lot * lot_multiple_ton
        if shortage_anchored_qty > 0 and qty >= shortage_anchored_qty:
            break
        specs.append((f"MOQ+{n_lot}lot", qty))

    specs.append(("shortage_anchored", shortage_anchored_qty))
    return specs


def generate_candidate_df(decision_master_df: pd.DataFrame, cfg: PipelineConfig) -> pd.DataFrame:
    """후보안 세트 생성.

    기본 후보
    ---------
    - observe
    - MOQ
    - MOQ+Nlot ladder
    - shortage_anchored (required candidate 성격)

    주의
    ----
    - 최종 목적은 "정답 1개"를 내는 게 아니라, 비교 가능한 후보안을 세우는 것이다.
    - shortage_anchored는 now row의 문제 규모를 실제로 반영해야 한다.
    - 중간 ladder 후보는 실무형 탐색력을 높이기 위해 추가한다.
    """
    rows: List[Dict] = []

    for _, row in decision_master_df.iterrows():
        moq_ton = float(row.get("moq_ton", cfg.moq_ton))
        lot_multiple_ton = float(row.get("lot_multiple_ton", cfg.lot_multiple_ton))
        now_cost = float(row.get(cfg.current_landed_cost_col, 0.0))

        arrival_month_idx = int(row.get("candidate_arrival_month_idx", cfg.lt_months))
        arrival_month_idx = max(1, min(arrival_month_idx, cfg.horizon_months))

        shortage_anchored_qty = derive_shortage_anchored_qty(row, cfg)

        candidate_specs = [("observe", 0.0)]
        candidate_specs.extend(
            build_ladder_candidates(
                shortage_anchored_qty=shortage_anchored_qty,
                moq_ton=moq_ton,
                lot_multiple_ton=lot_multiple_ton,
            )
        )

        seen_qty = set()
        for candidate_name, qty_ton in candidate_specs:
            normalized_qty = 0.0 if qty_ton == 0 else normalize_nonzero_candidate(qty_ton, moq_ton, lot_multiple_ton)
            if normalized_qty in seen_qty:
                continue
            seen_qty.add(normalized_qty)

            rows.append(
                {
                    "decision_id": row["decision_id"],
                    "material_code": row.get("material_code", cfg.material_code),
                    "candidate_name": candidate_name,
                    "candidate_qty_ton": normalized_qty,
                    "candidate_arrival_month_idx": arrival_month_idx if normalized_qty > 0 else 0,
                    "candidate_unit_cost_per_ton_now": now_cost,
                    "candidate_po_value_now": normalized_qty * now_cost,
                    "required_buy_qty_arrival_ton": float(row.get("required_buy_qty_arrival_ton", np.nan)),
                    "max_cum_gap_arrival_ton": float(row.get("max_cum_gap_arrival_ton", np.nan)),
                    "baseline_total_shortage_ton": float(row.get("baseline_total_shortage_ton", np.nan)),
                }
            )

    return pd.DataFrame(rows)
```

## 4) 기대 효과

현재 케이스(필요량 약 55,000톤)에서는 기존 후보가
- 0
- 5,000
- 7,500
- 55,000

뿐이라서 중간 해상도가 너무 낮았습니다.

패치 후에는 예를 들면
- 10,000
- 12,500
- 15,000
- 17,500
- ...
- 40,000
- 55,000

이 추가되어, gate 통과 / shortage 감소 / cost 증가 사이의 trade-off를 더 촘촘하게 볼 수 있습니다.

## 5) 이 수정의 한계

- 후보군이 늘어도 arrival timing tight 자체가 사라지는 것은 아닙니다.
- warehouse gate가 빡빡하면 큰 후보는 계속 blocked일 수 있습니다.
- 즉 이 수정은 “문제를 해결”한다기보다 “실행가능한 중간안 탐색력”을 높이는 수정입니다.
