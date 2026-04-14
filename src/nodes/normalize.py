"""
normalize_input 노드

종목코드·종목명을 표준화하고 State에 기록한다.
- 종목코드: 6자리 zero-padding, 앞의 ' 제거
- 종목명: pykrx로 검색 가능한 표준명 반환 (입력값 그대로 폴백)
- max_retry 기본값 세팅
"""
from __future__ import annotations

import logging

from src.state import DividendAgentState
from src.config import MAX_RETRY

logger = logging.getLogger(__name__)


def normalize_input(state: DividendAgentState) -> dict:
    """
    State에서 ticker / company_name을 받아 정규화된 값을 반환한다.
    LangGraph 노드 함수 — dict 반환 시 State에 머지된다.
    """
    raw_ticker: str = str(state.get("ticker", "")).strip().lstrip("'")
    company_name: str = str(state.get("company_name", "")).strip()

    # 종목코드 6자리 zero-padding
    ticker = raw_ticker.zfill(6)

    # pykrx로 종목명 검증 (실패 시 입력값 그대로 사용)
    verified_name = _verify_company_name(ticker, company_name)

    # max_retry 초기화 (이미 설정된 경우 유지)
    max_retry = state.get("max_retry") or MAX_RETRY

    logger.info("normalize_input: %s → ticker=%s name=%s", raw_ticker, ticker, verified_name)

    return {
        "ticker": ticker,
        "company_name": verified_name,
        "retry_count": state.get("retry_count", 0),
        "max_retry": max_retry,
        "sources": [],
    }


def _verify_company_name(ticker: str, fallback_name: str) -> str:
    """pykrx로 종목코드에 해당하는 공식 종목명을 조회한다."""
    try:
        from pykrx import stock
        name = stock.get_market_ticker_name(ticker)
        if name:
            return name
    except Exception as exc:
        logger.warning("pykrx 종목명 조회 실패 ticker=%s: %s", ticker, exc)
    return fallback_name
