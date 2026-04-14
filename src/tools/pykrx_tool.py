"""
pykrx 기반 배당 이력 수집 도구

pykrx get_market_fundamental()에서 제공하는 DIV(배당수익률)와
해당 날짜 종가를 이용해 주당 배당금을 역산한다.

역산 공식:
    dividend_amount = close_price × DIV / 100
    (DIV는 연간 배당수익률 %, 배당락일 기준 종가 사용)
"""
from __future__ import annotations

import time
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def get_dividend_history(
    ticker: str,
    start_year: int,
    end_year: int,
) -> dict[int, dict]:
    """
    종목의 연도별 배당 이력을 수집한다.

    Parameters
    ----------
    ticker     : 6자리 종목코드 (zero-padded, 예: "005930")
    start_year : 수집 시작 연도 (포함)
    end_year   : 수집 종료 연도 (포함)

    Returns
    -------
    dict  {연도(int): {"dividend_amount": float, "dividend_yield": float,
                       "source": "pykrx", "ticker": str}}
    무배당 연도 또는 데이터 없는 연도는 결과에서 제외된다.
    """
    from pykrx import stock  # 지연 임포트 — 미설치 환경 대비

    result: dict[int, dict] = {}

    for year in range(start_year, end_year + 1):
        try:
            data = _fetch_year(stock, ticker, year)
            if data:
                result[year] = data
        except Exception as exc:
            logger.warning("pykrx fetch 실패 ticker=%s year=%d: %s", ticker, year, exc)
        time.sleep(0.3)  # pykrx 과부하 방지

    return result


def _fetch_year(stock_module, ticker: str, year: int) -> Optional[dict]:
    """연도 단위 배당 수익률·주당 배당금 역산."""
    bgn = f"{year}0101"
    end = f"{year}1231"

    df: pd.DataFrame = stock_module.get_market_fundamental(bgn, end, ticker)

    if df is None or df.empty:
        return None

    # DIV > 0인 행(배당락일 부근)만 추출
    div_rows = df[df["DIV"] > 0]
    if div_rows.empty:
        return None

    # 배당락일 = DIV가 처음 0이 아닌 날짜 (보통 12월 말)
    ex_div_date = div_rows.index[-1]   # 가장 마지막 배당락일

    # 배당락일 종가
    price_df = stock_module.get_market_ohlcv(
        ex_div_date.strftime("%Y%m%d"),
        ex_div_date.strftime("%Y%m%d"),
        ticker,
    )
    if price_df is None or price_df.empty:
        close_price = None
        dividend_amount = None
    else:
        close_price = float(price_df["종가"].iloc[0])
        div_yield = float(div_rows.loc[ex_div_date, "DIV"])
        dividend_amount = round(close_price * div_yield / 100, 2)

    return {
        "dividend_amount": dividend_amount,
        "dividend_yield": float(div_rows["DIV"].max()),
        "ex_dividend_date": ex_div_date.strftime("%Y-%m-%d"),
        "close_price_on_ex_date": close_price,
        "source": "pykrx",
        "ticker": ticker,
    }
