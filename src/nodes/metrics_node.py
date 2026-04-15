"""
calculate_metrics 노드

검증 통과 후 최종 배당 지표를 계산하고 State를 확정한다.
- DART > 웹 순서로 필드 합성
- 배당락일 자동 계산 (없을 때)
"""
from __future__ import annotations

import logging

from src.state import DividendAgentState
from src.tools.validator import validate_ex_dividend_date

logger = logging.getLogger(__name__)


def calculate_metrics(state: DividendAgentState) -> dict:
    """
    두 소스를 합성해 최종 배당 데이터를 확정한다.

    우선순위: DART > 웹 검색
    """
    dart = state.get("extracted_from_dart") or {}
    web  = state.get("extracted_from_web")  or {}

    # 필드 합성 (DART 우선)
    dividend_amount   = dart.get("dividend_amount")   or web.get("dividend_amount")
    record_date       = dart.get("record_date")        or web.get("record_date")
    payment_date      = dart.get("payment_date")       or web.get("payment_date")
    ex_dividend_date  = dart.get("ex_dividend_date")   or web.get("ex_dividend_date")
    dividend_status   = dart.get("dividend_status")    or web.get("dividend_status") or "확정"

    # 배당락일 자동 계산 (기준일이 있고 락일이 없을 때)
    if record_date and not ex_dividend_date:
        val = validate_ex_dividend_date(record_date, "")
        ex_dividend_date = val.get("expected_ex_date")

    # 배당수익률 (DART alotMatter에서 가져옴)
    dividend_yield = dart.get("dividend_yield") or web.get("dividend_yield")

    logger.info(
        "calculate_metrics: %s %d → 배당금=%s원 기준일=%s 락일=%s 지급일=%s",
        state["company_name"], state["year"],
        dividend_amount, record_date, ex_dividend_date, payment_date,
    )

    return {
        "dividend_amount":  dividend_amount,
        "record_date":      record_date,
        "ex_dividend_date": ex_dividend_date,
        "payment_date":     payment_date,
        "dividend_status":  dividend_status,
        "dividend_yield":   dividend_yield,
    }
