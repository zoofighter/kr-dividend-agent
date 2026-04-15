"""
웹 검색 도구 — Naver 검색 API (1순위) / DuckDuckGo (폴백)

용도: DART에서 누락된 배당 날짜·금액을 웹에서 보완
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_NAVER_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"
_NAVER_WEB_URL    = "https://openapi.naver.com/v1/search/webkr.json"


# ── Naver 검색 ────────────────────────────────────────────────

def search_naver(query: str, display: int = 5) -> list[dict]:
    """
    Naver 검색 API로 뉴스 + 웹문서를 검색한다.

    Returns
    -------
    list[dict]  [{"title": str, "description": str, "url": str}]
    API 키 미설정 또는 오류 시 빈 리스트 반환
    """
    from src.config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET

    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise NaverAPIError("Naver API 키 미설정")

    headers = {
        "X-Naver-Client-Id":     NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    results = []

    # 뉴스 + 웹문서 2가지 검색
    for url in [_NAVER_SEARCH_URL, _NAVER_WEB_URL]:
        try:
            resp = requests.get(
                url,
                headers=headers,
                params={"query": query, "display": display, "sort": "sim"},
                timeout=10,
            )
            if resp.status_code == 429:
                raise NaverAPIError("Naver API 한도 초과")
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("items", []):
                results.append({
                    "title":       _strip_html(item.get("title", "")),
                    "description": _strip_html(item.get("description", "")),
                    "url":         item.get("link", ""),
                })
        except NaverAPIError:
            raise
        except Exception as exc:
            logger.debug("Naver 검색 오류 (%s): %s", url, exc)

    return results


class NaverAPIError(Exception):
    pass


# ── DuckDuckGo 폴백 ───────────────────────────────────────────

def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """
    DuckDuckGo 텍스트 검색 폴백.

    Returns
    -------
    list[dict]  [{"title": str, "description": str, "url": str}]
    """
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":       r.get("title", ""),
                    "description": r.get("body", ""),
                    "url":         r.get("href", ""),
                })
        return results
    except Exception as exc:
        logger.warning("DuckDuckGo 검색 오류: %s", exc)
        return []


# ── 통합 검색 (노드에서 호출) ─────────────────────────────────

def search_web(query: str, display: int = 5) -> tuple[list[dict], str]:
    """
    Naver 우선, 실패 시 DuckDuckGo 폴백.

    Returns
    -------
    (results, provider)  provider = "naver" | "duckduckgo"
    """
    try:
        results = search_naver(query, display)
        if results:
            return results, "naver"
    except NaverAPIError as exc:
        logger.info("Naver 폴백 → DuckDuckGo: %s", exc)

    results = search_duckduckgo(query, display)
    return results, "duckduckgo"


# ── 배당 관련 스니펫 필터 ─────────────────────────────────────

_DIVIDEND_KEYWORDS = re.compile(
    r"배당금|배당수익률|배당락|배당기준|배당지급|주당배당|DPS|dividend",
    re.IGNORECASE,
)


def filter_dividend_snippets(results: list[dict]) -> list[dict]:
    """배당 키워드가 포함된 결과만 반환한다."""
    return [r for r in results if _DIVIDEND_KEYWORDS.search(
        r.get("title", "") + " " + r.get("description", "")
    )]


def format_snippets(results: list[dict]) -> str:
    """LLM 프롬프트용 스니펫 텍스트 포맷."""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] 제목: {r['title']}")
        lines.append(f"    내용: {r['description']}")
        lines.append(f"    URL: {r['url']}")
    return "\n".join(lines)


# ── 내부 유틸 ──────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """HTML 태그 제거."""
    return re.sub(r"<[^>]+>", "", text).strip()
