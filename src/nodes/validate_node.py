"""
검증 노드

validate_result   : DART vs 웹 교차검증 → valid / retry / manual_review 판정
build_retry_query : 불일치 원인 → LLM으로 재검색 쿼리 생성
"""
from __future__ import annotations

import logging

from src.state import DividendAgentState
from src.tools.validator import validate_ex_dividend_date
from src.config import AMOUNT_TOLERANCE, LOCAL_LLM_MODEL, LOCAL_LLM_BASE_URL
from src.prompts import RETRY_QUERY_PROMPT, VALIDATION_JUDGE_PROMPT

logger = logging.getLogger(__name__)


# ── 검증 라우팅 함수 (LangGraph conditional edge용) ────────────

def route_after_validation(state: DividendAgentState) -> str:
    """validate_result 이후 분기 결정."""
    return state.get("validation_status", "manual_review")


# ── validate_result ───────────────────────────────────────────

def validate_result(state: DividendAgentState) -> dict:
    """
    DART·웹 두 소스의 배당 데이터를 교차검증한다.

    검증 항목:
      1. 배당금 불일치 (허용 오차: ±AMOUNT_TOLERANCE원)
      2. 배당기준일 불일치
      3. 배당락일 규칙 위반 (기준일 - 1 영업일)

    반환:
      validation_status : "valid" | "retry" | "manual_review"
      confidence_score  : 0.0 ~ 1.0
    """
    dart = state.get("extracted_from_dart") or {}
    web  = state.get("extracted_from_web")  or {}

    issues: list[str] = []

    # ── 1. 배당금 비교 ────────────────────────────────────────
    dart_amt = _to_float(dart.get("dividend_amount"))
    web_amt  = _to_float(web.get("dividend_amount"))

    if dart_amt and web_amt:
        diff = abs(dart_amt - web_amt)
        if diff > AMOUNT_TOLERANCE:
            issues.append(
                f"배당금 불일치: DART={dart_amt}원 vs 웹={web_amt}원 (차이 {diff}원)"
            )
    elif not dart_amt and not web_amt:
        issues.append("배당금 미수집: 두 소스 모두 null")

    # ── 2. 배당기준일 비교 ────────────────────────────────────
    dart_rec = dart.get("record_date")
    web_rec  = web.get("record_date")

    if dart_rec and web_rec and dart_rec != web_rec:
        issues.append(f"배당기준일 불일치: DART={dart_rec} vs 웹={web_rec}")

    # ── 3. 배당락일 규칙 검증 ────────────────────────────────
    record_date   = dart_rec or web_rec
    ex_div_date   = dart.get("ex_dividend_date") or web.get("ex_dividend_date")

    if record_date and ex_div_date:
        val = validate_ex_dividend_date(record_date, ex_div_date)
        if not val["valid"]:
            issues.append(f"배당락일 규칙 위반: {val['reason']}")

    # ── 판정 ─────────────────────────────────────────────────
    retry_count = state.get("retry_count", 0)
    max_retry   = state.get("max_retry", 2)

    if not issues:
        status = "valid"
        reason = "모든 검증 통과"
    elif retry_count < max_retry:
        status = "retry"
        reason = " | ".join(issues)
    else:
        status = "manual_review"
        reason = f"최대 재시도({max_retry}회) 초과. 문제: " + " | ".join(issues)

    score = _calc_confidence(dart, web, issues, retry_count)

    # manual_review 시 LLM 판단 근거 생성
    judge_comment = ""
    if status == "manual_review" and issues:
        judge_comment = _generate_judge_comment(state, issues)

    logger.info("validate_result: status=%s score=%.2f issues=%d건", status, score, len(issues))

    return {
        "validation_status":  status,
        "validation_reason":  reason + (f"\n[판단근거] {judge_comment}" if judge_comment else ""),
        "confidence_score":   score,
    }


def _calc_confidence(dart: dict, web: dict, issues: list, retry_count: int) -> float:
    score = 1.0

    if not dart.get("dividend_amount"):
        score -= 0.3      # DART 배당금 없음
    if not dart.get("record_date"):
        score -= 0.1

    if web.get("dividend_amount") and not dart.get("dividend_amount"):
        score -= 0.1      # 웹 단독 보완 페널티

    score -= len(issues) * 0.1
    score -= retry_count * 0.05

    return round(max(0.0, min(1.0, score)), 2)


def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _generate_judge_comment(state: DividendAgentState, issues: list) -> str:
    """VALIDATION_JUDGE_PROMPT로 수동 검토용 판단 근거를 생성한다."""
    try:
        from langchain_community.chat_models import ChatOllama
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        llm = ChatOllama(
            model=LOCAL_LLM_MODEL,
            base_url=LOCAL_LLM_BASE_URL,
            temperature=0,
        )
        prompt = ChatPromptTemplate.from_template(VALIDATION_JUDGE_PROMPT)
        chain  = prompt | llm | StrOutputParser()

        return chain.invoke({
            "company": state["company_name"],
            "year":    state["year"],
            "issues":  "\n".join(f"- {i}" for i in issues),
            "dart":    str(state.get("extracted_from_dart", {})),
            "web":     str(state.get("extracted_from_web", {})),
        })
    except Exception as exc:
        logger.warning("판단근거 생성 실패: %s", exc)
        return ""


# ── build_retry_query ─────────────────────────────────────────

def build_retry_query(state: DividendAgentState) -> dict:
    """
    검증 실패 원인을 분석해 DART 재검색 쿼리를 생성하고
    retry_count를 증가시킨다.
    """
    retry_count = (state.get("retry_count") or 0) + 1

    query = _generate_retry_query(state)
    logger.info("build_retry_query: retry=%d query='%s'", retry_count, query)

    return {
        "retry_query":  query,
        "retry_count":  retry_count,
    }


def _generate_retry_query(state: DividendAgentState) -> str:
    """RETRY_QUERY_PROMPT로 재검색 쿼리를 생성한다."""
    try:
        from langchain_community.chat_models import ChatOllama
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        llm = ChatOllama(
            model=LOCAL_LLM_MODEL,
            base_url=LOCAL_LLM_BASE_URL,
            temperature=0,
        )
        prompt = ChatPromptTemplate.from_template(RETRY_QUERY_PROMPT)
        chain  = prompt | llm | StrOutputParser()

        result = chain.invoke({
            "company":            state["company_name"],
            "year":               state["year"],
            "validation_reason":  state.get("validation_reason", ""),
            "extracted_from_dart": str(state.get("extracted_from_dart", {})),
            "extracted_from_web":  str(state.get("extracted_from_web", {})),
        })
        return result.strip().strip('"').strip("'")[:50]

    except Exception as exc:
        logger.warning("재검색 쿼리 생성 실패: %s", exc)
        # 폴백: 기본 쿼리
        return f"{state['company_name']} {state['year']} 배당 사업보고서"
