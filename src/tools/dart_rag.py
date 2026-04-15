"""
DART 배당 데이터 수집 도구

2단계 전략:
  1. 구조화 API (alotMatter.json)  — 배당금·배당수익률·배당성향 직접 수집
  2. 공시 원문 파싱               — 배당기준일·배당지급일 등 날짜 수집
  3. RAG 청크 구성                — LLM 추출용 텍스트 조합 (날짜 등 누락 필드 보완)

DART Open API 엔드포인트:
  alotMatter.json  : 배당에 관한 사항 (배당금, 수익률, 성향)
  document.xml     : 공시 원문 ZIP (HTML)
  list.json        : 공시 목록 조회
"""
from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"

# 사업보고서 연도별 공시 코드
REPRT_CODES = {
    "annual":  "11011",   # 사업보고서
    "half":    "11012",   # 반기보고서
    "q1":      "11013",   # 1분기보고서
    "q3":      "11014",   # 3분기보고서
}

# ── dart-fss 법인 목록 캐시 ─────────────────────────────────────
_corp_list = None


def _get_corp_list():
    global _corp_list
    if _corp_list is None:
        import dart_fss as dart
        from src.config import DART_API_KEY
        dart.set_api_key(DART_API_KEY)
        _corp_list = dart.get_corp_list()
    return _corp_list


def _get_corp_code(company_name: str) -> Optional[str]:
    """종목명 → DART corp_code 변환."""
    try:
        corp_list = _get_corp_list()
        corps = corp_list.find_by_corp_name(company_name, exactly=True)
        if corps:
            return corps[0].corp_code
    except Exception as exc:
        logger.warning("corp_code 조회 실패 %s: %s", company_name, exc)
    return None


def _dart_get(endpoint: str, params: dict) -> dict:
    """DART API GET 호출."""
    from src.config import DART_API_KEY
    params["crtfc_key"] = DART_API_KEY
    resp = requests.get(f"{DART_BASE}/{endpoint}", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── 1. 구조화 API — 배당금·수익률·성향 ─────────────────────────

def fetch_alot_matter(corp_code: str, year: int) -> Optional[dict]:
    """
    alotMatter.json 으로 배당에 관한 사항을 수집한다.

    Returns
    -------
    dict 또는 None
    {
      "dividend_amount": float,       # 주당 현금배당금 (보통주, 원)
      "dividend_yield": float,        # 현금배당수익률 (보통주, %)
      "payout_ratio": float,          # 현금배당성향 (%)
      "record_date": str,             # 결산일 (YYYY-MM-DD)
      "rcept_no": str,
    }
    """
    for reprt_code in [REPRT_CODES["annual"], REPRT_CODES["half"]]:
        try:
            data = _dart_get("alotMatter.json", {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt_code,
            })
            if data.get("status") != "000":
                continue

            items = data.get("list", [])
            result = _parse_alot_items(items)
            if result:
                return result
        except Exception as exc:
            logger.debug("alotMatter 조회 실패 year=%d code=%s: %s", year, reprt_code, exc)

    return None


def _parse_alot_items(items: list) -> Optional[dict]:
    """alotMatter API 응답에서 보통주 배당 정보를 추출한다."""
    result = {}
    for item in items:
        se = item.get("se", "")
        thstrm = item.get("thstrm", "-").replace(",", "").strip()
        stock_knd = item.get("stock_knd", "")

        # 주당 현금배당금 (보통주)
        if "주당 현금배당금" in se and stock_knd == "보통주":
            try:
                result["dividend_amount"] = float(thstrm)
            except ValueError:
                pass

        # 현금배당수익률 (보통주)
        elif "현금배당수익률" in se and stock_knd == "보통주":
            try:
                result["dividend_yield"] = float(thstrm)
            except ValueError:
                pass

        # 현금배당성향
        elif "현금배당성향" in se:
            try:
                result["payout_ratio"] = float(thstrm)
            except ValueError:
                pass

        # 결산일
        stlm_dt = item.get("stlm_dt", "")
        if stlm_dt and "record_date" not in result:
            result["record_date"] = stlm_dt

        # rcept_no
        if item.get("rcept_no") and "rcept_no" not in result:
            result["rcept_no"] = item["rcept_no"]

    return result if result.get("dividend_amount") else None


# ── 2. 공시 원문 파싱 — 배당락일·지급일 ────────────────────────

def fetch_dividend_dates(corp_code: str, year: int) -> dict:
    """
    '현금·현물배당결정' 공시 원문에서 배당기준일·지급일을 파싱한다.

    삼성전자처럼 분기 배당 종목은 연 4회 공시 → 페이지네이션 필요.
    연말(12월) 기준일이 포함된 공시 우선 반환.

    Returns
    -------
    dict {"ex_dividend_date": str|None, "payment_date": str|None,
          "record_date": str|None, "rcept_no": str|None}
    """
    from src.config import DART_API_KEY

    # 배당결정 공시 전체 수집 (여러 페이지 순회)
    target_nos = []
    try:
        for page in range(1, 15):   # 최대 14페이지 × 40건 = 560건
            data = _dart_get("list.json", {
                "corp_code": corp_code,
                "bgn_de": f"{year}0101",
                "end_de": f"{year + 1}0630",
                "page_count": "40",
                "page_no": str(page),
            })
            items = data.get("list", [])
            for f in items:
                nm = f.get("report_nm", "")
                if any(k in nm for k in ["배당결정", "현금ㆍ현물배당"]):
                    target_nos.append((f["rcept_dt"], f["rcept_no"]))
            if len(items) < 40:
                break   # 마지막 페이지
    except Exception as exc:
        logger.warning("공시 목록 조회 실패: %s", exc)
        return {}

    if not target_nos:
        return {}

    # 날짜순 정렬 후 파싱 — 12월 결산 기준일이 있는 공시 우선
    target_nos.sort(key=lambda x: x[0])   # rcept_dt 오름차순
    best: dict = {}

    for rcept_dt, rcept_no in target_nos:
        dates = _parse_dividend_doc(rcept_no, DART_API_KEY)
        if not dates.get("payment_date") and not dates.get("record_date"):
            continue
        dates["rcept_no"] = rcept_no
        # 결산 배당(12월 기준일)이면 즉시 반환
        rec = dates.get("record_date", "")
        if rec and rec.endswith("-12-31"):
            return dates
        # 아니면 후보로 보관 (더 좋은 것 탐색)
        if not best:
            best = dates

    return best


def _parse_dividend_doc(rcept_no: str, api_key: str) -> dict:
    """공시 ZIP 원문 HTML에서 날짜 정보를 파싱한다."""
    try:
        resp = requests.get(
            f"{DART_BASE}/document.xml",
            params={"crtfc_key": api_key, "rcept_no": rcept_no},
            timeout=20,
        )
        if not resp.content[:2] == b'PK':
            return {}

        z = zipfile.ZipFile(io.BytesIO(resp.content))
        html_bytes = z.read(z.namelist()[0])

        # EUC-KR 디코딩
        for enc in ("euc-kr", "cp949", "utf-8"):
            try:
                html = html_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return {}

        return _extract_dates_from_html(html)

    except Exception as exc:
        logger.debug("공시 파싱 오류 %s: %s", rcept_no, exc)
        return {}


def _extract_dates_from_html(html: str) -> dict:
    """HTML 테이블에서 배당 날짜를 정규식으로 추출한다."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")

    result = {}
    date_pat = re.compile(r"\d{4}-\d{2}-\d{2}")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        following = lines[i + 1] if i + 1 < len(lines) else ""
        date_match = date_pat.search(following) or date_pat.search(line)

        if "기준일" in line and "record_date" not in result:
            m = date_pat.search(following) or date_pat.search(line)
            if m:
                result["record_date"] = m.group()

        elif "지급" in line and "일" in line and "payment_date" not in result:
            m = date_pat.search(following) or date_pat.search(line)
            if m:
                result["payment_date"] = m.group()

        elif "락" in line and "ex_dividend_date" not in result:
            m = date_pat.search(following) or date_pat.search(line)
            if m:
                result["ex_dividend_date"] = m.group()

    return result


# ── 3. 통합 수집 함수 (노드에서 호출) ──────────────────────────

def search_dart_disclosure(
    company_name: str,
    year: int,
    query: str = "",          # 하위 호환용 (현재 미사용)
    top_k: int = 5,           # 하위 호환용 (현재 미사용)
) -> list[dict]:
    """
    DART에서 배당 데이터를 수집하고 청크 리스트로 반환한다.
    (LangGraph 노드와 인터페이스 유지)

    Returns
    -------
    list[dict]  [{"content": str, "source": str, "score": float}]
    """
    corp_code = _get_corp_code(company_name)
    if not corp_code:
        logger.warning("corp_code 없음: %s", company_name)
        return []

    # 1. 구조화 배당금 데이터
    alot = fetch_alot_matter(corp_code, year)

    # 2. 날짜 데이터
    dates = fetch_dividend_dates(corp_code, year)

    if not alot and not dates:
        return []

    # 3. 텍스트 청크 조합 (LLM 추출용)
    lines = [f"[DART 배당 데이터] {company_name} {year}년"]
    if alot:
        lines += [
            f"주당 현금배당금(보통주): {alot.get('dividend_amount')}원",
            f"현금배당수익률(보통주): {alot.get('dividend_yield')}%",
            f"현금배당성향: {alot.get('payout_ratio')}%",
            f"결산일: {alot.get('record_date')}",
        ]
    if dates:
        lines += [
            f"배당기준일: {dates.get('record_date')}",
            f"배당락일: {dates.get('ex_dividend_date')}",
            f"배당지급일: {dates.get('payment_date')}",
        ]

    content = "\n".join(filter(lambda x: "None" not in x, lines))
    source = alot.get("rcept_no") or dates.get("rcept_no") or "DART"

    return [{"content": content, "source": f"DART:{source}", "score": 1.0}]
