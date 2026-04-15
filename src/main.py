"""
배당 데이터 수집 에이전트 — 배치 실행 진입점
로컬 LLM(Ollama) 기반
"""
from __future__ import annotations

import logging
import sys

import pandas as pd

from src.config import (
    START_YEAR, END_YEAR,
    LOCAL_LLM_MODEL, LOCAL_LLM_BASE_URL,
    DART_API_KEY, OUTPUT_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def load_stock_list(path: str = "data/stock_list.xlsx") -> list[tuple[str, str]]:
    """
    stock_list.xlsx를 읽어 (종목코드, 종목명) 리스트를 반환한다.
    종목코드 앞의 ' 제거 및 6자리 zero-padding 적용.
    """
    df = pd.read_excel(path, dtype=str)
    df["종목코드"] = df["종목코드"].str.lstrip("'").str.zfill(6)
    return list(zip(df["종목코드"], df["종목명"]))


def run_batch(
    stock_list: list[tuple[str, str]],
    start_year: int = START_YEAR,
    end_year: int   = END_YEAR,
):
    """종목×연도 조합을 순회하며 그래프를 실행한다."""
    from src.graph import get_graph
    from src.nodes.save_node import get_results, get_manual_review, clear_results
    from src.tools.excel_tool import save_to_excel

    graph = get_graph()
    clear_results()

    total = len(stock_list) * (end_year - start_year + 1)
    done  = 0

    for ticker, company in stock_list:
        for year in range(start_year, end_year + 1):
            thread_id = f"{ticker}_{year}"
            initial_state = {
                "ticker":       ticker,
                "company_name": company,
                "year":         year,
                "retry_count":  0,
                "max_retry":    2,
            }
            config = {"configurable": {"thread_id": thread_id}}

            try:
                graph.invoke(initial_state, config=config)
            except Exception as exc:
                logger.error("실행 오류 %s %d: %s", company, year, exc)

            done += 1
            if done % 10 == 0:
                logger.info("진행 %d / %d", done, total)

    # 엑셀 저장
    path = save_to_excel(get_results(), get_manual_review(), OUTPUT_DIR)
    logger.info("완료: valid=%d  manual=%d  → %s",
                len(get_results()), len(get_manual_review()), path)
    return path


def main():
    print("dividend-agent start")
    print(f"  LLM  : {LOCAL_LLM_MODEL} @ {LOCAL_LLM_BASE_URL}")
    print(f"  기간  : {START_YEAR} ~ {END_YEAR}")
    print(f"  DART : {'설정됨' if DART_API_KEY else '미설정 (.env 확인 필요)'}")

    stock_list = load_stock_list()
    print(f"  종목수 : {len(stock_list)}개")
    print(f"  첫 종목: {stock_list[0]}")


if __name__ == "__main__":
    main()
