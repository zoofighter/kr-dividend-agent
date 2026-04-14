"""
배당 데이터 수집 에이전트 — 배치 실행 진입점
로컬 LLM(Ollama) 기반
"""
import pandas as pd

from src.config import (
    START_YEAR, END_YEAR,
    LOCAL_LLM_MODEL, LOCAL_LLM_BASE_URL,
    DART_API_KEY,
)


def load_stock_list(path: str = "data/stock_list.xlsx") -> list[tuple[str, str]]:
    """
    stock_list.xlsx를 읽어 (종목코드, 종목명) 리스트를 반환한다.
    종목코드 앞의 ' 제거 및 6자리 zero-padding 적용.
    """
    df = pd.read_excel(path, dtype=str)
    df["종목코드"] = df["종목코드"].str.lstrip("'").str.zfill(6)
    return list(zip(df["종목코드"], df["종목명"]))


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
