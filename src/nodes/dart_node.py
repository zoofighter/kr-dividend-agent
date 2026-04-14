"""
DART 관련 노드

search_dart_rag       : DART 공시 검색 → dart_chunks 저장
extract_dividend_from_dart : LLM으로 배당 필드 구조화 추출
"""
from __future__ import annotations

import json
import logging
import re

from src.state import DividendAgentState
from src.tools.dart_rag import search_dart_disclosure
from src.config import RAG_TOP_K, LOCAL_LLM_MODEL, LOCAL_LLM_BASE_URL
from src.prompts import DART_EXTRACT_PROMPT

logger = logging.getLogger(__name__)


def search_dart_rag(state: DividendAgentState) -> dict:
    """
    DART 공시를 RAG로 검색하고 관련 청크를 state에 저장한다.
    retry 시 retry_query를 쿼리로 사용한다.
    """
    company = state["company_name"]
    year = state["year"]

    query = state.get("retry_query") or f"{company} {year} 배당 배당금 배당기준일"

    logger.info("search_dart_rag: %s %d 쿼리='%s'", company, year, query)

    chunks = search_dart_disclosure(
        company_name=company,
        year=year,
        query=query,
        top_k=RAG_TOP_K,
    )

    sources = list(state.get("sources") or [])
    if chunks:
        sources.append("dart")

    return {
        "dart_chunks": chunks,
        "dart_query": query,
        "sources": sources,
    }


def extract_dividend_from_dart(state: DividendAgentState) -> dict:
    """
    DART RAG 청크에서 LLM으로 배당 필드를 구조화 추출한다.
    청크가 없으면 빈 dict를 반환한다.
    """
    chunks = state.get("dart_chunks") or []

    if not chunks:
        logger.warning("extract_dividend_from_dart: dart_chunks 없음 — 추출 스킵")
        return {"extracted_from_dart": {}}

    # 청크를 하나의 텍스트로 합치기
    chunks_text = "\n\n---\n\n".join(c["content"] for c in chunks)

    try:
        result = _run_llm_extraction(
            chunks_text=chunks_text,
            company_name=state["company_name"],
            year=state["year"],
        )
        logger.info("DART 추출 결과: %s", result)
    except Exception as exc:
        logger.error("DART LLM 추출 오류: %s", exc)
        result = {}

    return {"extracted_from_dart": result}


def _run_llm_extraction(chunks_text: str, company_name: str, year: int) -> dict:
    """Ollama LLM으로 DART 청크에서 배당 필드를 추출한다."""
    from langchain_community.chat_models import ChatOllama
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    llm = ChatOllama(
        model=LOCAL_LLM_MODEL,
        base_url=LOCAL_LLM_BASE_URL,
        temperature=0,
        format="json",
    )

    prompt = ChatPromptTemplate.from_template(DART_EXTRACT_PROMPT)
    chain = prompt | llm | StrOutputParser()

    raw = chain.invoke({
        "dart_chunks": chunks_text[:6000],   # 토큰 제한 대비 잘라서 전달
        "company_name": company_name,
        "year": year,
    })

    return _parse_json_safe(raw)


def _parse_json_safe(text: str) -> dict:
    """LLM 출력에서 JSON 파싱 — 마크다운 코드블록 제거 후 시도."""
    # ```json ... ``` 블록 제거
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = re.sub(r"```\s*$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # { ... } 블록만 추출 재시도
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.warning("JSON 파싱 실패, 원문: %s", text[:200])
    return {}
