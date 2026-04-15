"""
웹 검색 노드

search_web: Naver / DuckDuckGo로 배당 정보 보완 검색
            → LLM으로 배당 필드 추출 → extracted_from_web 저장
"""
from __future__ import annotations

import json
import logging
import re

from src.state import DividendAgentState
from src.tools.web_search import (
    search_web as _search_web,
    filter_dividend_snippets,
    format_snippets,
)
from src.config import LOCAL_LLM_MODEL, LOCAL_LLM_BASE_URL
from src.prompts import WEB_EXTRACT_PROMPT

logger = logging.getLogger(__name__)


def search_web(state: DividendAgentState) -> dict:
    """
    웹에서 배당 정보를 검색하고 LLM으로 필드를 추출한다.
    DART 데이터가 완전하면 웹 검색을 스킵한다.
    """
    company = state["company_name"]
    year    = state["year"]

    # DART 데이터가 이미 완전하면 스킵
    dart = state.get("extracted_from_dart") or {}
    if _is_dart_complete(dart):
        logger.info("search_web 스킵: DART 데이터 완전 (%s %d)", company, year)
        return {"extracted_from_web": {}, "web_search_results": [], "web_search_provider": "skipped"}

    query = f"{company} {year} 배당금 배당기준일 배당지급일"
    logger.info("search_web: %s", query)

    raw_results, provider = _search_web(query, display=5)
    filtered = filter_dividend_snippets(raw_results)

    if not filtered:
        logger.info("웹 검색 결과 없음 (배당 키워드 미포함)")
        return {
            "extracted_from_web": {},
            "web_search_results": raw_results,
            "web_search_provider": provider,
        }

    # LLM으로 배당 필드 추출
    extracted = _extract_from_snippets(filtered, company, year)

    sources = list(state.get("sources") or [])
    if extracted:
        sources.append(f"web:{provider}")

    return {
        "extracted_from_web": extracted,
        "web_search_results": filtered,
        "web_search_provider": provider,
        "sources": sources,
    }


def _is_dart_complete(dart: dict) -> bool:
    """DART 추출 결과에 핵심 필드가 모두 있는지 확인한다."""
    required = ["dividend_amount", "record_date"]
    return all(dart.get(f) for f in required)


def _extract_from_snippets(snippets: list[dict], company: str, year: int) -> dict:
    """Ollama LLM으로 웹 스니펫에서 배당 필드를 추출한다."""
    try:
        from langchain_community.chat_models import ChatOllama
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        llm = ChatOllama(
            model=LOCAL_LLM_MODEL,
            base_url=LOCAL_LLM_BASE_URL,
            temperature=0,
            format="json",
        )
        prompt = ChatPromptTemplate.from_template(WEB_EXTRACT_PROMPT)
        chain  = prompt | llm | StrOutputParser()

        raw = chain.invoke({
            "snippets":     format_snippets(snippets[:5]),
            "company_name": company,
            "year":         year,
        })
        return _parse_json_safe(raw)

    except Exception as exc:
        logger.error("웹 LLM 추출 오류: %s", exc)
        return {}


def _parse_json_safe(text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = re.sub(r"```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {}
