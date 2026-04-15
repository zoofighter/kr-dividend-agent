"""
저장 노드

save_result        : 검증 통과 데이터를 결과 리스트에 추가
mark_manual_review : 수동 검토 항목으로 분류
"""
from __future__ import annotations

import logging

from src.state import DividendAgentState

logger = logging.getLogger(__name__)

# 배치 실행 시 누적 저장소 (graph.py에서 참조)
_results: list[dict] = []
_manual_review: list[dict] = []


def get_results() -> list[dict]:
    return _results


def get_manual_review() -> list[dict]:
    return _manual_review


def clear_results():
    _results.clear()
    _manual_review.clear()


def save_result(state: DividendAgentState) -> dict:
    """검증 통과 데이터를 결과 목록에 저장한다."""
    row = _build_row(state)
    row["validation_status"] = "valid"
    _results.append(row)

    logger.info(
        "save_result: %s %d 저장 완료 (누적 %d건)",
        state["company_name"], state["year"], len(_results),
    )
    return {"saved": True}


def mark_manual_review(state: DividendAgentState) -> dict:
    """자동 검증 실패 항목을 수동 검토 목록에 저장한다."""
    row = _build_row(state)
    row["validation_status"] = "manual_review"
    row["validation_reason"] = state.get("validation_reason", "")
    _manual_review.append(row)

    logger.info(
        "mark_manual_review: %s %d 수동검토 분류 (누적 %d건)",
        state["company_name"], state["year"], len(_manual_review),
    )
    return {"saved": True, "validation_status": "manual_review"}


def _build_row(state: DividendAgentState) -> dict:
    """State에서 저장할 필드를 추출한다."""
    return {
        "ticker":           state.get("ticker", ""),
        "company_name":     state.get("company_name", ""),
        "year":             state.get("year", 0),
        "dividend_amount":  state.get("dividend_amount"),
        "dividend_yield":   state.get("dividend_yield"),
        "ex_dividend_date": state.get("ex_dividend_date"),
        "record_date":      state.get("record_date"),
        "payment_date":     state.get("payment_date"),
        "dividend_status":  state.get("dividend_status", ""),
        "confidence_score": state.get("confidence_score", 0.0),
        "sources":          ", ".join(state.get("sources") or []),
        "retry_count":      state.get("retry_count", 0),
        "validation_reason": state.get("validation_reason", ""),
    }
