"""
배당 날짜 검증 도구

한국 주식시장 규칙:
    배당락일 = 배당기준일 - 1 영업일 (XKRX 캘린더 기준)
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# XKRX 캘린더를 모듈 수준에서 한 번만 초기화
_xkrx_calendar = None


def _get_calendar():
    global _xkrx_calendar
    if _xkrx_calendar is None:
        import pandas_market_calendars as mcal
        _xkrx_calendar = mcal.get_calendar("XKRX")
    return _xkrx_calendar


def validate_ex_dividend_date(
    record_date: str,
    ex_dividend_date: str,
) -> dict:
    """
    배당락일이 배당기준일 - 1 영업일인지 검증한다.

    Parameters
    ----------
    record_date      : 배당기준일 (YYYY-MM-DD)
    ex_dividend_date : 배당락일   (YYYY-MM-DD)

    Returns
    -------
    dict  {"valid": bool, "expected_ex_date": str, "actual_ex_date": str,
           "reason": str}
    """
    if not record_date or not ex_dividend_date:
        return {
            "valid": False,
            "expected_ex_date": None,
            "actual_ex_date": ex_dividend_date,
            "reason": "record_date 또는 ex_dividend_date가 None입니다",
        }

    try:
        record_ts = pd.Timestamp(record_date)
        actual_ts = pd.Timestamp(ex_dividend_date)
    except Exception as exc:
        return {
            "valid": False,
            "expected_ex_date": None,
            "actual_ex_date": ex_dividend_date,
            "reason": f"날짜 파싱 오류: {exc}",
        }

    expected_ts = _prev_business_day(record_ts)
    is_valid = expected_ts == actual_ts

    return {
        "valid": is_valid,
        "expected_ex_date": expected_ts.strftime("%Y-%m-%d"),
        "actual_ex_date": actual_ts.strftime("%Y-%m-%d"),
        "reason": (
            "배당락일 정합"
            if is_valid
            else f"불일치: 기대={expected_ts.strftime('%Y-%m-%d')}, 실제={actual_ts.strftime('%Y-%m-%d')}"
        ),
    }


def _prev_business_day(ts: pd.Timestamp) -> pd.Timestamp:
    """XKRX 캘린더 기준으로 ts의 직전 영업일을 반환한다."""
    try:
        cal = _get_calendar()
        # ts 전날부터 30일 전까지 범위에서 영업일 목록 조회
        start = (ts - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        end   = (ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        schedule = cal.schedule(start_date=start, end_date=end)
        if schedule.empty:
            # 캘린더 조회 실패 시 단순 BDay 폴백
            return _bday_fallback(ts)
        return pd.Timestamp(schedule.index[-1].date())
    except Exception as exc:
        logger.warning("XKRX 캘린더 오류, BDay 폴백: %s", exc)
        return _bday_fallback(ts)


def _bday_fallback(ts: pd.Timestamp) -> pd.Timestamp:
    """pandas BDay 오프셋 폴백 (공휴일 미반영)."""
    from pandas.tseries.offsets import BDay
    return ts - BDay(1)
