---
tags:
  - LangGraph
  - 도구함수
  - Tools
  - 배당
created: 2026-04-12
related:
  - "[[requirements]]"
  - "[[nodes]]"
---

# 배당 데이터 수집 에이전트 — 도구 함수(Tools) 상세 설명

> [!important] 검색 관련 도구 함수의 품질이 에이전트 전체 정확도를 결정한다.

---

## 도구 목록 한눈에 보기

| # | 도구 함수 | 사용 노드 | 역할 |
|---|-----------|----------|------|
| 1 | `search_dart_disclosure` | `search_dart_rag` | DART 공시 → RAG 청크 추출 |
| 2 | `get_dividend_history` | `fetch_pykrx_history` | pykrx 과거 10년 배당 이력 |
| 3 | `normalize_ticker` | `normalize_input` | 종목코드·종목명 표준화 |
| 4 | `validate_ex_dividend_date` | `validate_result` | 배당락일 규칙 검증 |
| 5 | `search_naver` | `search_web` | Naver API 웹 검색 |
| 6 | `search_duckduckgo` | `search_web` | DuckDuckGo 폴백 검색 |
| 7 | `save_to_excel` | `save_result` | 결과 엑셀 저장 |

---

## 도구 1 — `search_dart_disclosure`

### 개요
DART(전자공시시스템) 공시 문서를 수집하고, **RAG**로 배당 관련 청크만 반환한다.
에이전트 정확도의 핵심 도구다.

### 시그니처

```python
@tool
def search_dart_disclosure(
    company_name: str,
    year: int,
    query: str,
    report_type: str = "사업보고서",
) -> list[dict]:
    """
    DART 공시에서 배당 관련 청크를 RAG로 검색한다.

    Args:
        company_name : 회사명 (예: "삼성전자")
        year         : 대상 연도 (예: 2024)
        query        : 검색 쿼리 (예: "주당배당금 배당기준일")
        report_type  : 보고서 종류 (사업보고서 / 반기보고서 / 배당결정공시)

    Returns:
        [{"content": str, "source": str, "score": float}, ...]
    """
```

### 내부 파이프라인

```
1. DART API 호출
   └─ dart-fss 또는 DART OpenAPI
   └─ 보고서 유형 필터 (A=사업보고서, F=반기보고서)
   └─ 기간 필터: {year}0101 ~ {year}1231

2. 문서 청크 분할
   └─ RecursiveCharacterTextSplitter
   └─ chunk_size=500, overlap=50

3. 임베딩 생성
   └─ OpenAIEmbeddings (기본)
   └─ HuggingFaceEmbeddings (로컬 LLM 사용 시)

4. VectorStore 저장
   └─ FAISS (기본, 인메모리)
   └─ Chroma (영속 저장 필요 시)

5. 유사도 검색
   └─ similarity_search(query, k=5)
   └─ 관련 청크 반환
```

### 구현 코드

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
import dart_fss as dart

@tool
def search_dart_disclosure(
    company_name: str,
    year: int,
    query: str,
    report_type: str = "사업보고서",
) -> list[dict]:
    report_code = {"사업보고서": "A", "반기보고서": "F"}.get(report_type, "A")

    # 1. DART 공시 수집
    corp = dart.get_corp_list().find_by_corp_name(company_name)[0]
    reports = dart.filings.get_list(
        corp_code=corp.corp_code,
        bgn_de=f"{year}0101",
        end_de=f"{year}1231",
        pblntf_ty=report_code,
    )

    # 2. 전체 텍스트 추출
    full_text = "\n".join(r.to_file().read_text() for r in reports[:3])

    # 3. 청크 분할
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    docs = splitter.create_documents([full_text])

    # 4. 벡터 스토어 생성 및 검색
    vectorstore = FAISS.from_documents(docs, OpenAIEmbeddings())
    results = vectorstore.similarity_search_with_score(query, k=5)

    return [
        {"content": doc.page_content, "source": doc.metadata.get("source", ""), "score": float(score)}
        for doc, score in results
    ]
```

### 보고서 유형별 우선순위

| 유형 | 코드 | 언제 사용 |
|------|------|----------|
| 사업보고서 | `A` | 기본 (연간 배당 확정) |
| 반기보고서 | `F` | 중간배당 데이터 필요 시 |
| 배당결정공시 | 별도 검색 | 최신 연도, 이사회 결정 직후 |

### 주의 사항
- DART API는 일일 호출 한도 있음 → 동일 문서는 인메모리 캐싱 후 재사용
- 공시 문서가 없으면 빈 리스트 `[]` 반환 (노드에서 null 처리)
- 재시도 시 새 쿼리로 **동일 VectorStore 재검색** 가능 (API 재호출 불필요)

---

## 도구 2 — `get_dividend_history`

### 개요
**pykrx 라이브러리**로 특정 종목의 연도별 배당 이력을 빠르게 조회한다.
DART 공시와 교차 검증하는 2차 소스이며, 배당금 역산에 사용한다.

### 시그니처

```python
@tool
def get_dividend_history(
    ticker: str,
    start_year: int,
    end_year: int,
) -> dict:
    """
    pykrx로 특정 종목의 연도별 배당 이력을 조회한다.

    Args:
        ticker     : 종목코드 6자리 (예: "005930")
        start_year : 시작 연도 (예: 2015)
        end_year   : 종료 연도 (예: 2025)

    Returns:
        {
          2024: {"dividend_amount": 1444, "dividend_yield": 2.3, "close_price": 62800},
          2023: {...},
          ...
        }
    """
```

### 배당금 역산 공식

```
pykrx DIV = 연간 배당수익률 (%)
배당금(역산) = 배당락일 종가 × DIV / 100

예: 종가 62,800원 × 2.3% / 100 = 1,444원
```

### 구현 코드

```python
from pykrx import stock
import pandas as pd

@tool
def get_dividend_history(
    ticker: str,
    start_year: int,
    end_year: int,
) -> dict:
    result = {}

    for year in range(start_year, end_year + 1):
        df = stock.get_market_fundamental(
            f"{year}0101", f"{year}1231", ticker
        )
        if df.empty:
            continue

        # DIV가 가장 높은 날 = 배당락일 근방 추정
        max_div_row = df.loc[df["DIV"].idxmax()]
        close_price = stock.get_market_ohlcv(
            max_div_row.name.strftime("%Y%m%d"),
            max_div_row.name.strftime("%Y%m%d"),
            ticker,
        )["종가"].iloc[0]

        result[year] = {
            "dividend_yield": float(max_div_row["DIV"]),
            "dividend_amount": round(close_price * max_div_row["DIV"] / 100),
            "close_price": int(close_price),
            "estimated_ex_date": max_div_row.name.strftime("%Y-%m-%d"),
        }

    return result
```

### 주의 사항
- pykrx DIV는 **역산값**이므로 DART 확정 배당금과 ±10원 오차는 정상
- 무배당 연도는 `DIV = 0.0` → 해당 연도 건너뜀
- 상장폐지 또는 상장 전 연도는 빈 데이터 반환

---

## 도구 3 — `normalize_ticker`

### 개요
종목명 또는 코드를 받아 **표준 6자리 코드, 공식 회사명, 시장 구분**을 반환한다.
`normalize_input` 노드의 핵심 도구.

### 시그니처

```python
@tool
def normalize_ticker(query: str) -> dict:
    """
    종목명 또는 코드를 표준 코드와 회사명으로 변환한다.

    Args:
        query : 종목명 또는 코드 (예: "삼성전자", "005930", "삼성전자우")

    Returns:
        {
          "ticker": "005930",
          "company_name": "삼성전자",
          "market": "KOSPI",
          "is_preferred": False,
        }
    """
```

### 처리 케이스

| 입력 예시 | 처리 방식 | 출력 |
|----------|----------|------|
| `"삼성전자"` | pykrx 종목명 검색 | `005930` |
| `"005930"` | zero-padding 확인 후 반환 | `005930` |
| `"5930"` | zero-padding 적용 | `005930` |
| `"삼성전자우"` | 우선주 식별 | `005935`, `is_preferred=True` |
| `"SAMSUNG"` | 영문 → 한글 매핑 | `005930` |

### 구현 코드

```python
from pykrx import stock

@tool
def normalize_ticker(query: str) -> dict:
    # 숫자만 입력된 경우 → 코드로 처리
    if query.strip().isdigit():
        ticker = query.strip().zfill(6)
        name = stock.get_market_ticker_name(ticker)
        market = stock.get_market_ticker_list(market="KOSPI")
        return {
            "ticker": ticker,
            "company_name": name,
            "market": "KOSPI" if ticker in market else "KOSDAQ",
            "is_preferred": ticker.endswith("5"),
        }

    # 종목명으로 검색
    kospi_tickers = stock.get_market_ticker_list(market="KOSPI")
    kosdaq_tickers = stock.get_market_ticker_list(market="KOSDAQ")

    for ticker in kospi_tickers + kosdaq_tickers:
        name = stock.get_market_ticker_name(ticker)
        if query in name:
            return {
                "ticker": ticker,
                "company_name": name,
                "market": "KOSPI" if ticker in kospi_tickers else "KOSDAQ",
                "is_preferred": ticker.endswith("5"),
            }

    raise ValueError(f"종목을 찾을 수 없습니다: {query}")
```

### 주의 사항
- 우선주(`005935`)와 보통주(`005930`)는 **별개 ticker**로 처리
- DART 검색은 법인 단위이므로 우선주도 `company_name`은 동일 (`"삼성전자"`)
- 동명 회사 있을 경우 시가총액 상위 종목 우선 선택

---

## 도구 4 — `validate_ex_dividend_date`

### 개요
**한국 증시 규칙**에 따라 배당락일이 배당기준일 -1 영업일인지 검증한다.
`validate_result` 노드에서 날짜 규칙 위반 감지에 사용한다.

### 한국 배당 날짜 규칙

```
배당락일(ex-dividend date) = 배당기준일(record date) - 1 영업일
영업일 기준: 한국거래소 휴장일(공휴일, 주말) 제외
```

### 시그니처

```python
@tool
def validate_ex_dividend_date(
    record_date: str,
    ex_dividend_date: str,
) -> dict:
    """
    배당락일이 배당기준일 -1 영업일인지 검증한다.

    Args:
        record_date      : 배당기준일 (YYYY-MM-DD)
        ex_dividend_date : 배당락일 (YYYY-MM-DD)

    Returns:
        {
          "valid": True,
          "expected_ex_date": "2024-12-26",
          "actual_ex_date": "2024-12-26",
          "diff_days": 0,
        }
    """
```

### 구현 코드

```python
import pandas_market_calendars as mcal
import pandas as pd

@tool
def validate_ex_dividend_date(
    record_date: str,
    ex_dividend_date: str,
) -> dict:
    krx = mcal.get_calendar("XKRX")  # 한국거래소 캘린더
    record_dt = pd.Timestamp(record_date)
    ex_dt     = pd.Timestamp(ex_dividend_date)

    # 배당기준일 기준으로 -1 영업일 계산
    schedule = krx.schedule(
        start_date=(record_dt - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        end_date=record_date,
    )
    trading_days = schedule.index.tolist()
    expected_ex = trading_days[-2]  # record_date 바로 전 영업일

    diff = (ex_dt - expected_ex).days

    return {
        "valid": diff == 0,
        "expected_ex_date": expected_ex.strftime("%Y-%m-%d"),
        "actual_ex_date": ex_dividend_date,
        "diff_days": diff,
    }
```

### 검증 예시

```
배당기준일: 2024-12-27 (금)
예상 배당락일: 2024-12-26 (목, 영업일)
실제 배당락일: 2024-12-26 → valid: True

배당기준일: 2024-12-27 (금)
실제 배당락일: 2024-12-25 (수, 크리스마스 휴장) → valid: False (diff_days: -1)
```

### 주의 사항
- `pandas-market-calendars` 패키지 필요: `pip install pandas-market-calendars`
- 한국 공휴일은 매년 변경 → 라이브러리 최신 버전 유지 필요
- 2019년 이전 데이터는 T+2 결제 규칙 적용 (배당락일 = 기준일 - 2 영업일)

---

## 도구 5 — `search_naver`

### 개요
**Naver 검색 API**로 배당 관련 뉴스와 웹 문서를 검색한다.
`search_web` 노드의 1순위 웹 검색 도구.

### 시그니처

```python
@tool
def search_naver(
    query: str,
    display: int = 5,
    search_type: str = "news",
) -> list[dict]:
    """
    Naver 검색 API로 배당 관련 문서를 검색한다.

    Args:
        query       : 검색 쿼리 (예: "삼성전자 2024 배당금 배당기준일")
        display     : 결과 수 (기본 5)
        search_type : "news" (뉴스) 또는 "webkr" (웹 문서)

    Returns:
        [{"title": str, "description": str, "url": str, "pubDate": str}, ...]
    """
```

### API 설정

```python
# .env
NAVER_CLIENT_ID     = "your_client_id"
NAVER_CLIENT_SECRET = "your_client_secret"
```

```
발급: https://developers.naver.com/apps
무료 한도: 하루 25,000 콜
```

### 구현 코드

```python
import os
import requests
from bs4 import BeautifulSoup

class NaverAPIError(Exception):
    pass

@tool
def search_naver(
    query: str,
    display: int = 5,
    search_type: str = "news",
) -> list[dict]:
    url = f"https://openapi.naver.com/v1/search/{search_type}.json"
    headers = {
        "X-Naver-Client-Id":     os.environ["NAVER_CLIENT_ID"],
        "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"],
    }
    params = {"query": query, "display": display, "sort": "date"}

    resp = requests.get(url, headers=headers, params=params, timeout=5)

    if resp.status_code == 429:
        raise NaverAPIError("일일 호출 한도 초과")
    if resp.status_code != 200:
        raise NaverAPIError(f"API 오류: {resp.status_code}")

    items = resp.json().get("items", [])

    return [
        {
            "title":       BeautifulSoup(i["title"], "html.parser").get_text(),
            "description": BeautifulSoup(i["description"], "html.parser").get_text(),
            "url":         i.get("link") or i.get("originallink", ""),
            "pubDate":     i.get("pubDate", ""),
        }
        for i in items
    ]
```

### 주의 사항
- HTML 태그(`<b>`, `</b>`)가 title/description에 포함됨 → `BeautifulSoup`으로 제거
- 환경변수 미설정 시 `KeyError` 발생 → `search_web` 노드에서 `NaverAPIError`로 처리 후 DuckDuckGo 폴백
- 뉴스(`news`)와 웹(`webkr`) 두 타입 모두 시도하면 더 많은 정보 확보 가능

---

## 도구 6 — `search_duckduckgo`

### 개요
Naver API 실패 시 사용하는 **무료 폴백 검색 도구**.
API 키 없이 사용 가능하며, `duckduckgo-search` 라이브러리를 사용한다.

### 시그니처

```python
@tool
def search_duckduckgo(
    query: str,
    max_results: int = 5,
    region: str = "kr-ko",
) -> list[dict]:
    """
    DuckDuckGo로 배당 관련 문서를 검색한다 (Naver API 폴백).

    Args:
        query       : 검색 쿼리
        max_results : 최대 결과 수 (기본 5)
        region      : 지역 (기본 "kr-ko" 한국어)

    Returns:
        [{"title": str, "description": str, "url": str}, ...]
    """
```

### 설치

```bash
pip install duckduckgo-search
```

### 구현 코드

```python
from duckduckgo_search import DDGS

@tool
def search_duckduckgo(
    query: str,
    max_results: int = 5,
    region: str = "kr-ko",
) -> list[dict]:
    with DDGS() as ddgs:
        results = list(ddgs.text(
            query,
            region=region,
            max_results=max_results,
        ))

    return [
        {
            "title":       r.get("title", ""),
            "description": r.get("body", ""),
            "url":         r.get("href", ""),
        }
        for r in results
    ]
```

### Naver vs DuckDuckGo 비교

| 항목 | Naver 검색 API | DuckDuckGo |
|------|---------------|------------|
| API 키 | 필요 | 불필요 |
| 무료 한도 | 25,000콜/일 | 무제한 (비공식) |
| 한국어 품질 | ★★★ 우수 | ★★ 보통 |
| 속도 | 빠름 | 보통 |
| 안정성 | 공식 API | 비공식 스크래핑 |
| 사용 시점 | 1순위 | Naver 실패 시 폴백 |

### 주의 사항
- DuckDuckGo는 **비공식 스크래핑** 방식 → 요청 빈도 높으면 일시 차단 가능
- Rate limit 발생 시 `time.sleep(2)` 후 재시도
- 한국어 검색 품질이 Naver보다 낮으므로 **보완용**으로만 사용

---

## 도구 7 — `save_to_excel`

### 개요
수집·검증된 전체 결과를 **3개 시트 구성의 엑셀 파일**로 저장한다.
배치 실행 완료 후 최종 단계에서 1회 호출한다.

### 시그니처

```python
@tool
def save_to_excel(
    results: list[dict],
    validation_logs: list[dict],
    manual_review_items: list[dict],
    output_path: str,
) -> str:
    """
    수집 결과를 엑셀 파일로 저장한다.

    Args:
        results             : 검증 완료된 배당 데이터 (Sheet 1)
        validation_logs     : 검증 과정 로그 (Sheet 2)
        manual_review_items : 수동 확인 필요 항목 (Sheet 3)
        output_path         : 저장 경로 (예: "dividend_result_20260412.xlsx")

    Returns:
        저장된 파일의 절대 경로
    """
```

### 시트 구성

#### Sheet 1 — 배당 데이터 (메인)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| 종목코드 | str | 6자리 표준 코드 |
| 종목명 | str | 회사명 |
| 연도 | int | 기준 연도 |
| 배당금 | float | 주당 배당금 (원) |
| 배당수익률 | float | % |
| 배당락일 | date | ex-dividend date |
| 배당기준일 | date | record date |
| 배당지급일 | date | payment date |
| 배당예정 여부 | str | 확정 / 예정 / 미확정 |
| 데이터 출처 | str | DART / pykrx / 웹 / 복합 |
| 검증 상태 | str | 검증완료 / 재확인필요 / 수집실패 |
| 신뢰도 점수 | float | 0.0 ~ 1.0 |

#### Sheet 2 — 검증 로그

| 컬럼 | 설명 |
|------|------|
| 종목코드 | |
| 연도 | |
| 충돌 내용 | 어떤 값이 불일치했는지 |
| 재시도 횟수 | |
| 최종 판단 근거 | |

#### Sheet 3 — 수동 확인 필요 목록

| 컬럼 | 설명 |
|------|------|
| 종목코드 | |
| 연도 | |
| 충돌 내용 | |
| DART 수집값 | |
| pykrx 수집값 | |
| 웹 검색값 | |
| 근거 URL | |

### 구현 코드

```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from datetime import datetime

@tool
def save_to_excel(
    results: list[dict],
    validation_logs: list[dict],
    manual_review_items: list[dict],
    output_path: str,
) -> str:
    wb = openpyxl.Workbook()

    # ── Sheet 1: 배당 데이터 ──────────────────────────────
    ws1 = wb.active
    ws1.title = "배당 데이터"
    headers1 = [
        "종목코드", "종목명", "연도", "배당금", "배당수익률",
        "배당락일", "배당기준일", "배당지급일",
        "배당예정 여부", "데이터 출처", "검증 상태", "신뢰도 점수",
    ]
    _write_header(ws1, headers1)
    for r in results:
        ws1.append([
            r.get("ticker"), r.get("company_name"), r.get("year"),
            r.get("dividend_amount"), r.get("dividend_yield"),
            r.get("ex_dividend_date"), r.get("record_date"), r.get("payment_date"),
            r.get("dividend_status"), r.get("sources"),
            r.get("validation_status"), r.get("confidence_score"),
        ])

    # ── Sheet 2: 검증 로그 ────────────────────────────────
    ws2 = wb.create_sheet("검증 로그")
    headers2 = ["종목코드", "연도", "충돌 내용", "재시도 횟수", "최종 판단 근거"]
    _write_header(ws2, headers2)
    for log in validation_logs:
        ws2.append([
            log.get("ticker"), log.get("year"),
            log.get("validation_reason"), log.get("retry_count"),
            log.get("final_reason"),
        ])

    # ── Sheet 3: 수동 확인 필요 ───────────────────────────
    ws3 = wb.create_sheet("수동 확인 필요")
    headers3 = ["종목코드", "연도", "충돌 내용", "DART 수집값", "pykrx 수집값", "웹 검색값", "근거 URL"]
    _write_header(ws3, headers3)
    for item in manual_review_items:
        ws3.append([
            item.get("ticker"), item.get("year"),
            item.get("validation_reason"),
            str(item.get("extracted_from_dart", {})),
            str(item.get("extracted_from_pykrx", {})),
            str(item.get("extracted_from_web", {})),
            item.get("evidence_url", ""),
        ])

    wb.save(output_path)
    return str(output_path)


def _write_header(ws, headers: list[str]):
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D3557")
        cell.alignment = Alignment(horizontal="center")
```

### 주의 사항
- 날짜 컬럼(`배당락일` 등)은 `date` 타입으로 저장해야 엑셀에서 날짜 서식 자동 적용
- 50종목 × 10년 = 최대 500행으로 파일 크기 작음 → 단일 파일로 충분
- `output_path`는 `f"dividend_result_{datetime.today().strftime('%Y%m%d')}.xlsx"` 형태로 자동 생성 권장

---

## 도구 등록 및 에이전트 연결

```python
from langgraph.prebuilt import ToolNode

# 도구 목록
tools = [
    search_dart_disclosure,
    get_dividend_history,
    normalize_ticker,
    validate_ex_dividend_date,
    search_naver,
    search_duckduckgo,
    save_to_excel,
]

# LangGraph에 ToolNode로 등록
tool_node = ToolNode(tools)

# LLM에 도구 바인딩 (tool calling)
llm_with_tools = llm.bind_tools(tools)
```

---

## 의존 패키지 요약

```bash
pip install \
  dart-fss \
  pykrx \
  langchain langchain-openai langchain-community \
  faiss-cpu \
  pandas-market-calendars \
  duckduckgo-search \
  openpyxl \
  beautifulsoup4 \
  requests
```
