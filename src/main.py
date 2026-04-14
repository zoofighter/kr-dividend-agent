"""
배당 데이터 수집 에이전트 — 배치 실행 진입점
로컬 LLM(Ollama) 기반
"""
from src.config import (
    START_YEAR, END_YEAR,
    LOCAL_LLM_MODEL, LOCAL_LLM_BASE_URL,
    DART_API_KEY,
)


def main():
    print("dividend-agent start")
    print(f"  LLM  : {LOCAL_LLM_MODEL} @ {LOCAL_LLM_BASE_URL}")
    print(f"  기간  : {START_YEAR} ~ {END_YEAR}")
    print(f"  DART : {'설정됨' if DART_API_KEY else '미설정 (.env 확인 필요)'}")


if __name__ == "__main__":
    main()
