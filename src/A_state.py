"""
[FILE PURPOSE]
- A_state는 현재 유지 구매 상태를 만드는 파일이다.
- 현재고, Open PO, 입항/통관 상태, Landed Cost, 리드타임, MOQ를 구매 판단이 가능한
  상태 컬럼으로 변환한다.

[BUSINESS UNIT]
- 품목 × 산지 × 공급사 × 수입 구매 조건.
- 팜유/대두유/해바라기유/야자유의 재고 커버, 입고 리스크, 도착원가 기준을 다룬다.

[INPUT]
- data/edible_oil_market_inputs_demo.csv에서 읽은 market_df.
- current_inventory_ton, open_po_ton, monthly_usage_forecast_ton,
  lead_time_month, supplier_moq_ton, planned_order_ton,
  contract_price_usd_per_ton, tariff_rate_pct, customs_fee_krw_per_ton,
  inland_freight_krw_per_ton, warehouse_fee_krw_per_ton,
  document_ready, inbound_delay_days.

[OUTPUT]
- B_interpret 단계로 넘길 상태 DataFrame.
- landed_cost_krw_per_ton, inbound_status, effective_available_inventory_ton,
  at_risk_open_po_ton, required_cover_month, inventory_cover_month,
  shortage_expected, moq_check.

[현업 적용 시 교체 대상]
- synthetic/demo 데이터는 SAP MM 구매오더, ERP/WMS 재고, 포워더 ETD/ETA,
  통관 서류 상태, 보세창고 입고 예정일, 관세/통관비/내륙운송비/창고비 데이터로 대체한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ProcurementConfig


REQUIRED_COLUMNS = [
    "as_of_month",
    "item_name",
    "current_inventory_ton",
    "monthly_usage_forecast_ton",
    "open_po_ton",
    "lead_time_month",
    "supplier_moq_ton",
    "planned_order_ton",
    "contract_price_usd_per_ton",
    "tariff_rate_pct",
    "customs_fee_krw_per_ton",
    "inland_freight_krw_per_ton",
    "warehouse_fee_krw_per_ton",
    "document_ready",
    "inbound_delay_days",
]


# ============================================================
# [BLOCK] 입력 컬럼 검증
# [업무 의미] 구매 판단에 필요한 최소 데이터가 빠졌는지 먼저 확인한다.
# [판단 기준] 현재고, Open PO, 사용량, 리드타임, MOQ, 원가/통관 비용 컬럼이 있어야 한다.
# [산출물] 누락 컬럼이 있으면 명확한 ValueError로 중단한다.
# [수정 포인트] 현업 데이터 연동 시 필수 컬럼 정의를 사내 표준명에 맞춰 매핑한다.
# [WHY] 잘못된 입력으로 구매 액션을 만들면 발주 판단 자체가 위험해진다.
# [ASSUMPTION] demo CSV는 REQUIRED_COLUMNS를 모두 포함한다.
# [DESIGN LOGIC] 컬럼 검증만 수행하고 값 보정은 이후 상태 계산 블록에서 처리한다.
# [DATA LINEAGE] data/edible_oil_market_inputs_demo.csv의 헤더와 직접 연결된다.
# [REAL DATA REPLACEMENT] SAP/WMS/통관 데이터 컬럼명을 이 리스트에 맞게 변환한다.
# [INTERVIEW CHECK] 데이터 품질 게이트를 먼저 둔 이유를 설명할 수 있다.
# ============================================================
def _require_columns(df: pd.DataFrame) -> None:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Input data is missing required columns: {missing}")


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


# ============================================================
# [BLOCK] 입항/통관 상태 판단
# [업무 의미] Open PO가 있어도 실제 생산 투입 가능한 재고인지 구분한다.
# [판단 기준] 지연일이 크거나 서류가 준비되지 않으면 AT_RISK, 경미한 지연은 WATCH.
# [산출물] inbound_status: ON_TRACK, WATCH, AT_RISK.
# [수정 포인트] 선사, 포워더, 통관사 SLA에 맞춰 지연 기준을 조정한다.
# [WHY] Open PO 전체를 재고처럼 보면 입항/통관 지연에 따른 결품 리스크를 놓친다.
# [ASSUMPTION] demo에서는 inbound_delay_days와 document_ready만으로 상태를 단순화한다.
# [DESIGN LOGIC] 서류 미완료는 일정 지연 가능성이 커서 AT_RISK로 보수적으로 본다.
# [DATA LINEAGE] document_ready, inbound_delay_days 컬럼을 직접 반영한다.
# [REAL DATA REPLACEMENT] 포워더 ETA, 통관 진행 상태, 서류 수리 여부로 대체한다.
# [INTERVIEW CHECK] Open PO와 가용재고를 분리한 이유를 설명하는 핵심 블록이다.
# ============================================================
def _inbound_status(row: pd.Series) -> str:
    delay_days = float(row.get("inbound_delay_days", 0) or 0)
    document_ready = _as_bool(row.get("document_ready", False))

    if delay_days >= 10 or not document_ready:
        return "AT_RISK"
    if delay_days >= 4:
        return "WATCH"
    return "ON_TRACK"


def build_current_purchase_state(
    market_df: pd.DataFrame,
    cfg: ProcurementConfig,
) -> pd.DataFrame:
    """Create the current procurement state for each edible oil item."""
    _require_columns(market_df)
    out = market_df.copy()
    out["as_of_month"] = pd.to_datetime(out["as_of_month"]).dt.to_period("M").dt.to_timestamp()

    # ============================================================
    # [BLOCK] Landed Cost 계산
    # [업무 의미] 계약 단가만 보지 않고 실제 회사 원가율에 들어오는 도착원가를 계산한다.
    # [판단 기준] 계약가, 환율, 관세, 통관비, 내륙운송비, 창고비를 모두 반영한다.
    # [산출물] landed_cost_krw_per_ton.
    # [수정 포인트] 실제 적용 시 품목별 관세율, Incoterms, 보험료, 항만 부대비를 추가한다.
    # [WHY] 구매 의사결정은 단순 원물 가격이 아니라 원가율 방어 관점의 도착원가 기준이어야 한다.
    # [ASSUMPTION] demo에는 usdkrw가 없을 수 있어 default_usdkrw를 사용한다.
    # [DESIGN LOGIC] USD 계약가를 KRW로 환산한 뒤 관세와 국내 부대비를 더한다.
    # [DATA LINEAGE] contract_price_usd_per_ton, tariff_rate_pct, customs/inland/warehouse fee.
    # [REAL DATA REPLACEMENT] SAP 계약단가, 환율 테이블, 관세 DB, 물류비 정산 데이터.
    # [INTERVIEW CHECK] "가격 예측이 아니라 Landed Cost 기반 구매 판단"이라고 설명한다.
    # ============================================================
    usdkrw = out["usdkrw"] if "usdkrw" in out.columns else cfg.default_usdkrw
    base_cost = out["contract_price_usd_per_ton"] * usdkrw
    tariff_cost = base_cost * (out["tariff_rate_pct"] / 100)
    out["landed_cost_krw_per_ton"] = (
        base_cost
        + tariff_cost
        + out["customs_fee_krw_per_ton"]
        + out["inland_freight_krw_per_ton"]
        + out["warehouse_fee_krw_per_ton"]
    ).round(0)

    # ============================================================
    # [BLOCK] Open PO 리스크 반영 가용재고 계산
    # [업무 의미] 예정 입고 물량을 전부 재고로 보지 않고 입항/통관 리스크를 반영한다.
    # [판단 기준] AT_RISK는 Open PO를 가용재고에서 제외하고, WATCH는 50%만 반영한다.
    # [산출물] inbound_status, at_risk_open_po_ton, effective_available_inventory_ton.
    # [수정 포인트] 실제로는 선적 확정, 통관 단계, 보세창고 입고 확률에 따라 가중치를 세분화한다.
    # [WHY] 구매팀은 "발주되어 있음"과 "생산에 쓸 수 있음"을 분리해 결품을 방어해야 한다.
    # [ASSUMPTION] demo에서는 입고 상태를 세 단계로만 단순화한다.
    # [DESIGN LOGIC] 위험한 Open PO는 보수적으로 제외해 결품 리스크를 과소평가하지 않는다.
    # [DATA LINEAGE] open_po_ton, document_ready, inbound_delay_days.
    # [REAL DATA REPLACEMENT] 포워더 tracking, 통관 시스템, WMS 입고 예정 데이터.
    # [INTERVIEW CHECK] Open PO 전체 반영 X, 리스크 조정 가용재고 O라고 설명한다.
    # ============================================================
    out["inbound_status"] = out.apply(_inbound_status, axis=1)
    out["at_risk_open_po_ton"] = np.where(out["inbound_status"] == "AT_RISK", out["open_po_ton"], 0.0)
    effective_open_po = np.where(
        out["inbound_status"] == "AT_RISK",
        0.0,
        np.where(out["inbound_status"] == "WATCH", out["open_po_ton"] * 0.5, out["open_po_ton"]),
    )
    out["effective_available_inventory_ton"] = (out["current_inventory_ton"] + effective_open_po).round(1)
    out["required_cover_month"] = (out["lead_time_month"] + cfg.safety_stock_month).round(2)

    # ============================================================
    # [BLOCK] 재고 커버와 결품 예상 판단
    # [업무 의미] 현재 구매 상태가 리드타임과 안전재고를 버틸 수 있는지 판단한다.
    # [판단 기준] effective_available_inventory_ton / 월 사용량으로 커버 개월 수를 만들고,
    #             required_cover_month보다 짧으면 shortage_expected로 본다.
    # [산출물] inventory_cover_month, lead_time_gap_month, shortage_expected.
    # [수정 포인트] 실제 적용 시 판매계획/SOP, 생산계획, 프로모션 수요를 사용량에 반영한다.
    # [WHY] 결품 위험은 현재고 부족이 아니라 "입고 전까지 버틸 수 있는가"의 문제다.
    # [ASSUMPTION] demo 월 사용량은 monthly_usage_forecast_ton 하나로 대표한다.
    # [DESIGN LOGIC] 리드타임+안전재고보다 커버가 짧으면 구매 액션 검토 대상으로 올린다.
    # [DATA LINEAGE] effective_available_inventory_ton, monthly_usage_forecast_ton, lead_time_month.
    # [REAL DATA REPLACEMENT] ERP 재고, WMS 가용재고, 생산계획/MRP 소요량.
    # [INTERVIEW CHECK] shortage_expected가 단순 재고량이 아니라 시간 기준 판단임을 설명한다.
    # ============================================================
    usage = out["monthly_usage_forecast_ton"].replace(0, np.nan)
    out["inventory_cover_month"] = (
        out["effective_available_inventory_ton"] / usage
    ).replace([np.inf, -np.inf], np.nan)
    out["inventory_cover_month"] = out["inventory_cover_month"].fillna(0).round(2)
    out["lead_time_gap_month"] = (out["lead_time_month"] - out["inventory_cover_month"]).round(2)
    out["shortage_expected"] = out["inventory_cover_month"] < out["required_cover_month"]

    # 계획 발주량이 공급사 MOQ에 미달하면 단독 발주가 어려워 묶음 발주나 MOQ 협상이 필요하다.
    out["moq_check"] = out["planned_order_ton"] >= (out["supplier_moq_ton"] * cfg.moq_tolerance)

    return out
