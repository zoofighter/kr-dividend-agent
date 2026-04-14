"""
LangGraph 상태 정의 — DividendAgentState
"""
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class DividendAgentState(TypedDict, total=False):
    # ── 입력 식별자 ────────────────────────────────
    ticker: str               # 종목코드 (6자리, zero-padded)
    company_name: str         # 종목명
    year: int                 # 수집 대상 연도

    # ── 대화/추론 기록 (LangGraph 표준) ────────────
    messages: Annotated[list, add_messages]

    # ── DART RAG 검색 결과 ─────────────────────────
    dart_chunks: list         # DART RAG 검색 결과 청크
    dart_query: str           # 사용한 검색 쿼리

    # ── pykrx 과거 데이터 ──────────────────────────
    pykrx_history: dict       # {year: {dividend, div_yield, ...}}

    # ── 추출된 구조화 값 ───────────────────────────
    dividend_amount: float    # 주당 배당금 (원)
    ex_dividend_date: str     # 배당락일 (YYYY-MM-DD)
    record_date: str          # 배당기준일 (YYYY-MM-DD)
    payment_date: str         # 배당지급일 (YYYY-MM-DD)
    expected_dividend_date: str  # 예정일 (미확정 시)
    dividend_status: str      # 확정 / 예정 / 미확정

    # ── 가공 지표 ──────────────────────────────────
    dividend_yield: float     # 배당 수익률 (%)

    # ── 소스별 추출값 (교차 검증용) ────────────────
    extracted_from_dart: dict
    extracted_from_pykrx: dict
    extracted_from_web: dict

    # ── 웹 검색 ────────────────────────────────────
    web_search_results: list       # 원본 검색 결과 스니펫
    web_search_provider: str       # "naver" / "duckduckgo"

    # ── 검증 제어 상태 ─────────────────────────────
    validation_status: str    # valid / retry / manual_review
    validation_reason: str    # 충돌 이유 상세
    retry_query: str          # 재검색 쿼리
    retry_count: int          # 현재 재시도 횟수
    max_retry: int            # 최대 재시도 횟수 (기본: 2)

    # ── 신뢰도 ─────────────────────────────────────
    confidence_score: float   # 0.0 ~ 1.0
    sources: list             # 사용한 소스 목록

    # ── 최종 저장 ──────────────────────────────────
    saved: bool
