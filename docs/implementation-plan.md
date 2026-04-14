---
tags:
  - LangGraph
  - 구현계획
  - 배당
created: 2026-04-14
related:
  - "[[requirements]]"
  - "[[nodes]]"
  - "[[tools]]"
  - "[[prompts]]"
---

# 배당 데이터 수집 에이전트 — 구현 계획서

> [!summary] 목표
> 문서화 완료된 설계를 바탕으로 실제 동작하는 코드를 단계적으로 구현한다.
> 단계마다 독립적으로 검증 가능한 산출물을 만들고, 이전 단계가 통과된 후 다음 단계로 진행한다.

---

## 전체 일정 개요

| 단계 | 내용 | 완료 기준 |
|------|------|----------|
| **Phase 0** | 프로젝트 뼈대 · 환경 설정 | `python main.py` 오류 없이 실행 |
| **Phase 1** | State · 종목 정규화 · pykrx 수집 | 1개 종목 배당 이력 터미널 출력 |
| **Phase 2** | DART RAG 구현 | 공시 청크 추출 및 LLM 필드 추출 동작 |
| **Phase 3** | 웹 검색 보완 | Naver / DuckDuckGo 스니펫 추출 동작 |
| **Phase 4** | 검증 루프 · LangGraph 조립 | 1개 종목-연도 전체 그래프 실행 |
| **Phase 5** | 엑셀 출력 · 배치 실행 | 50종목 × 10년 결과 파일 생성 |
| **Phase 6** | 검증 및 품질 개선 | 신뢰도 점수 분포 확인, 프롬프트 튜닝 |

---

## 프로젝트 파일 구조

```
dividend-agent/
├── src/
│   ├── config.py              # 환경변수, 상수 (연도 범위, max_retry 등)
│   ├── state.py               # DividendAgentState TypedDict
│   ├── prompts.py             # 모든 프롬프트 상수 (DART_EXTRACT_PROMPT 등)
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── dart_rag.py        # search_dart_disclosure (DART RAG)
│   │   ├── pykrx_tool.py      # get_dividend_history
│   │   ├── web_search.py      # search_naver, search_duckduckgo
│   │   ├── excel_tool.py      # save_to_excel
│   │   └── validator.py       # validate_ex_dividend_date
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── normalize.py       # normalize_input
│   │   ├── dart_node.py       # search_dart_rag, extract_dividend_from_dart
│   │   ├── pykrx_node.py      # fetch_pykrx_history
│   │   ├── web_node.py        # search_web
│   │   ├── validate_node.py   # validate_result, build_retry_query
│   │   ├── metrics_node.py    # calculate_metrics
│   │   └── save_node.py       # save_result, mark_manual_review
│   ├── graph.py               # LangGraph 그래프 조립 및 컴파일
│   └── main.py                # 배치 실행 진입점
├── data/
│   └── stock_list.xlsx        # 50개 종목 리스트 (기존 파일)
├── output/                    # 생성된 엑셀 파일 저장 위치
├── checkpoint.db              # LangGraph SqliteSaver 체크포인트 (자동 생성)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Phase 0 — 프로젝트 뼈대 · 환경 설정

### 목표
코드를 한 줄도 작성하지 않은 상태에서 폴더 구조와 의존성을 세팅한다.

### 작업 목록

**0-1. 폴더 생성**
```
src/, src/tools/, src/nodes/, output/
```

**0-2. `requirements.txt` 작성**
```
langgraph>=0.2
langchain>=0.3
langchain-openai>=0.2
langchain-community>=0.3
faiss-cpu
dart-fss
pykrx
pandas-market-calendars
duckduckgo-search
openpyxl
beautifulsoup4
requests
python-dotenv
```

**0-3. `.env.example` 작성**
```
OPENAI_API_KEY=sk-...
DART_API_KEY=...
NAVER_CLIENT_ID=...        # 선택
NAVER_CLIENT_SECRET=...    # 선택
```

**0-4. `src/config.py` 작성**
```python
# 수집 연도 범위
START_YEAR = 2016
END_YEAR   = 2025

# 검증 정책
MAX_RETRY  = 2
AMOUNT_TOLERANCE = 10      # 배당금 허용 오차 (원)

# DART RAG 설정
CHUNK_SIZE    = 500
CHUNK_OVERLAP = 50
RAG_TOP_K     = 5

# 출력
OUTPUT_DIR = "output"
```

**0-5. `src/state.py` 작성**
- `requirements.md` Section 4-3의 `DividendAgentState` TypedDict 그대로 구현

**0-6. `src/prompts.py` 작성**
- `prompts.md`의 5개 프롬프트 상수를 그대로 옮김
- `PROMPT_VERSION = "v1.0"` 추가

**0-7. `src/main.py` 뼈대 작성**
```python
# 실행만 되면 OK — 아직 로직 없음
if __name__ == "__main__":
    print("dividend-agent start")
```

### 완료 기준
```bash
pip install -r requirements.txt  # 오류 없음
python src/main.py               # "dividend-agent start" 출력
```

---

## Phase 1 — 종목 정규화 · pykrx 배당 이력 수집

### 목표
`data/stock_list.xlsx`를 읽어 1개 종목의 10년치 배당 이력을 터미널에 출력한다.

### 작업 순서

**1-1. `stock_list.xlsx` 파싱 코드**
`src/main.py`에 추가:
```python
import pandas as pd
df = pd.read_excel("data/stock_list.xlsx")
stock_list = list(zip(df["종목코드"].astype(str).str.zfill(6), df["종목명"]))
```
> 엑셀 컬럼명은 실제 파일 확인 후 맞춤

**1-2. `src/tools/pykrx_tool.py` 구현**
- `get_dividend_history(ticker, start_year, end_year) -> dict`
- `tools.md` 도구 2 코드 기반으로 구현
- 예외 처리: 상장폐지·데이터 없는 연도 → 빈 dict 반환

**1-3. `src/nodes/normalize.py` 구현**
- `normalize_input(state) -> dict`
- pykrx로 ticker → company_name, market 반환
- 6자리 zero-padding 처리

**1-4. `src/tools/validator.py` 구현**
- `validate_ex_dividend_date(record_date, ex_dividend_date) -> dict`
- `pandas-market-calendars` XKRX 캘린더 사용

**1-5. 단독 테스트 스크립트**
```python
# test_phase1.py
from src.tools.pykrx_tool import get_dividend_history
result = get_dividend_history("005930", 2016, 2025)
for year, data in sorted(result.items()):
    print(f"{year}: {data}")
```

### 완료 기준
- 삼성전자(`005930`) 10년치 배당 데이터 출력
- 무배당 연도는 건너뜀 확인
- `validate_ex_dividend_date` 샘플 검증 통과

---

## Phase 2 — DART RAG 구현

### 목표
1개 종목/연도에 대해 DART 공시를 수집하고 LLM으로 배당 필드를 추출한다.

### 전제 조건
- `DART_API_KEY` 발급 완료 (https://opendart.fss.or.kr)
- `OPENAI_API_KEY` 발급 완료

### 작업 순서

**2-1. DART API 연결 확인**
```python
import dart_fss as dart
dart.set_api_key(os.environ["DART_API_KEY"])
corp_list = dart.get_corp_list()
corp = corp_list.find_by_corp_name("삼성전자")[0]
print(corp.corp_code)
```

**2-2. `src/tools/dart_rag.py` 구현**
- `search_dart_disclosure(company_name, year, query, report_type) -> list[dict]`
- `tools.md` 도구 1 코드 기반
- 캐싱: 동일 종목-연도 VectorStore를 dict에 보관 → 재시도 시 재사용

**2-3. `src/nodes/dart_node.py` 구현**
- `search_dart_rag(state) -> dict`
  - `retry_query`가 있으면 사용, 없으면 기본 쿼리
  - 공시 없으면 `dart_chunks = []` 반환
- `extract_dividend_from_dart(state) -> dict`
  - `DART_EXTRACT_PROMPT` 사용
  - `dart_chunks`가 비어 있으면 LLM 호출 생략

**2-4. 단독 테스트 스크립트**
```python
# test_phase2.py
from src.tools.dart_rag import search_dart_disclosure
chunks = search_dart_disclosure("삼성전자", 2024, "주당배당금 배당기준일")
for c in chunks:
    print(c["content"][:200])
    print("---")
```

### 완료 기준
- 삼성전자 2024년 DART 청크 5개 이상 반환
- `extract_dividend_from_dart` 실행 시 `dividend_amount`가 정수로 반환

> [!warning] 주의
> DART API는 일일 호출 한도가 있다. 개발 중에는 1~2개 종목으로만 테스트한다.
> VectorStore 캐싱이 동작하는지 반드시 확인한다.

---

## Phase 3 — 웹 검색 보완 구현

### 목표
Naver / DuckDuckGo로 배당 뉴스를 검색하고 스니펫에서 배당 필드를 추출한다.

### 작업 순서

**3-1. `src/tools/web_search.py` 구현**
- `search_naver(query, display, search_type) -> list[dict]`
  - `tools.md` 도구 5 코드 기반
  - `NaverAPIError` 예외 클래스 포함
- `search_duckduckgo(query, max_results, region) -> list[dict]`
  - `tools.md` 도구 6 코드 기반

**3-2. `src/nodes/web_node.py` 구현**
- `search_web(state) -> dict`
  - Naver 우선 → 실패 시 DuckDuckGo 폴백
  - `_is_dividend_related(text)` 정규식 필터 함수 포함
  - `WEB_EXTRACT_PROMPT`로 스니펫 → 필드 추출

**3-3. NAVER_CLIENT_ID 미설정 케이스 확인**
- 환경변수 없어도 DuckDuckGo로 폴백되는지 테스트

### 완료 기준
- 삼성전자 2024 배당 관련 스니펫 3개 이상 반환
- `extracted_from_web`에 `dividend_amount` 또는 `record_date` 하나 이상 추출

---

## Phase 4 — 검증 루프 · LangGraph 그래프 조립

### 목표
모든 노드를 LangGraph로 연결하고, 1개 종목-연도에 대해 전체 흐름이 동작한다.

### 작업 순서

**4-1. `src/nodes/validate_node.py` 구현**
- `validate_result(state) -> dict`
  - 배당금 ±10원 비교
  - 배당락일 규칙 검증 (`validate_ex_dividend_date` 호출)
  - `validation_status`: `valid` / `retry` / `manual_review`
- `build_retry_query(state) -> dict`
  - `RETRY_QUERY_PROMPT` 또는 규칙 기반 쿼리 생성
  - `retry_count + 1`

**4-2. `src/nodes/metrics_node.py` 구현**
- `calculate_metrics(state) -> dict`
  - 배당 수익률 계산
  - 신뢰도 점수 산정 (nodes.md 노드 8 기준)

**4-3. `src/nodes/save_node.py` 구현**
- `save_result(state) -> dict`
  - 전역 결과 버퍼에 행 데이터 추가
  - `saved = True`
- `mark_manual_review(state) -> dict`
  - 전역 수동 검토 버퍼에 항목 추가

**4-4. `src/graph.py` 구현**

```python
from langgraph.graph import StateGraph, END
from src.state import DividendAgentState

def build_graph():
    builder = StateGraph(DividendAgentState)

    # 노드 등록
    builder.add_node("normalize_input", normalize_input)
    builder.add_node("search_dart_rag", search_dart_rag)
    builder.add_node("extract_dividend_from_dart", extract_dividend_from_dart)
    builder.add_node("fetch_pykrx_history", fetch_pykrx_history)
    builder.add_node("search_web", search_web)
    builder.add_node("validate_result", validate_result)
    builder.add_node("build_retry_query", build_retry_query)
    builder.add_node("calculate_metrics", calculate_metrics)
    builder.add_node("save_result", save_result)
    builder.add_node("mark_manual_review", mark_manual_review)

    # 엣지 설정
    builder.set_entry_point("normalize_input")
    builder.add_edge("normalize_input", "search_dart_rag")
    builder.add_edge("search_dart_rag", "extract_dividend_from_dart")
    builder.add_edge("extract_dividend_from_dart", "fetch_pykrx_history")
    builder.add_edge("fetch_pykrx_history", "search_web")
    builder.add_edge("search_web", "validate_result")
    builder.add_conditional_edges("validate_result", route_after_validation)
    builder.add_edge("build_retry_query", "search_dart_rag")
    builder.add_edge("calculate_metrics", "save_result")
    builder.add_edge("save_result", END)
    builder.add_edge("mark_manual_review", END)

    return builder.compile(checkpointer=SqliteSaver.from_conn_string("checkpoint.db"))
```

**4-5. 단일 종목-연도 테스트**
```python
# test_phase4.py
graph = build_graph()
result = graph.invoke(
    {"ticker": "005930", "company_name": "삼성전자", "year": 2024, "max_retry": 2},
    config={"configurable": {"thread_id": "005930_2024"}}
)
print(result["validation_status"])
print(result.get("dividend_amount"))
```

### 완료 기준
- 삼성전자 2024년: `validation_status == "valid"` 또는 `"manual_review"` 로 종료
- 재시도 루프 동작 확인 (의도적으로 `max_retry=0`으로 테스트)
- 체크포인트 DB 생성 확인

---

## Phase 5 — 엑셀 출력 · 배치 실행

### 목표
50종목 × 10년을 배치 실행하고 `dividend_result_YYYYMMDD.xlsx`를 생성한다.

### 작업 순서

**5-1. `src/tools/excel_tool.py` 구현**
- `save_to_excel(results, validation_logs, manual_review_items, output_path) -> str`
- `tools.md` 도구 7 코드 기반
- 헤더 스타일 적용 (진한 파란 배경, 흰 글씨)

**5-2. `src/main.py` 배치 실행 구현**

```python
from src.graph import build_graph
from src.tools.excel_tool import save_to_excel
from src.config import START_YEAR, END_YEAR
import pandas as pd
from datetime import datetime

def load_stock_list(path="data/stock_list.xlsx"):
    df = pd.read_excel(path)
    return list(zip(df["종목코드"].astype(str).str.zfill(6), df["종목명"]))

if __name__ == "__main__":
    graph = build_graph()
    stock_list = load_stock_list()

    results, validation_logs, manual_review_items = [], [], []

    for ticker, company in stock_list:
        for year in range(START_YEAR, END_YEAR + 1):
            thread_id = f"{ticker}_{year}"
            try:
                state = graph.invoke(
                    {"ticker": ticker, "company_name": company,
                     "year": year, "max_retry": 2},
                    config={"configurable": {"thread_id": thread_id}},
                )
                results.append(state)
                if state.get("validation_reason"):
                    validation_logs.append(state)
                if state.get("validation_status") == "manual_review":
                    manual_review_items.append(state)
            except Exception as e:
                print(f"[ERROR] {ticker} {year}: {e}")

    output_path = f"output/dividend_result_{datetime.today().strftime('%Y%m%d')}.xlsx"
    save_to_excel(results, validation_logs, manual_review_items, output_path)
    print(f"저장 완료: {output_path}")
```

**5-3. 소규모 배치 테스트**
- 5종목 × 3년으로 먼저 실행
- 엑셀 파일 열어서 3개 시트 데이터 확인

### 완료 기준
- 50종목 × 10년 실행 완료 (오류 있어도 `try/except`로 계속 진행)
- 엑셀 Sheet 1에 행 데이터 있음
- Sheet 3(수동 확인 필요)에 `manual_review` 항목 기록됨

---

## Phase 6 — 검증 및 품질 개선

### 목표
결과 데이터 품질을 점검하고 프롬프트와 검증 로직을 개선한다.

### 작업 순서

**6-1. 품질 점검 스크립트**
```python
import pandas as pd
df = pd.read_excel("output/dividend_result_YYYYMMDD.xlsx", sheet_name="배당 데이터")

print("전체 행:", len(df))
print("검증완료:", (df["검증 상태"] == "검증완료").sum())
print("재확인필요:", (df["검증 상태"] == "재확인필요").sum())
print("신뢰도 분포:\n", df["신뢰도 점수"].describe())
print("null 비율:\n", df.isnull().mean())
```

**6-2. 프롬프트 튜닝 기준**

| 문제 | 조치 |
|------|------|
| `dividend_amount` null 비율 > 20% | `DART_EXTRACT_PROMPT` 필드 정의 강화 |
| `manual_review` 비율 > 30% | `validate_result` 허용 오차 검토 |
| `evidence` 필드가 빈 문자열 | 프롬프트에 `evidence 필수` 재강조 |
| pykrx 배당금 역산값 오차 큰 종목 | 허용 오차 ±10원 → ±50원으로 완화 검토 |

**6-3. 개선 후 재실행**
- 체크포인트 DB 삭제 후 전체 재실행
- 또는 `manual_review` 항목만 골라서 재실행

### 완료 기준
- 검증완료 비율 ≥ 60%
- 핵심 필드(`dividend_amount`, `record_date`) null 비율 ≤ 30%

---

## 의존성 설치 순서

```bash
# 1. 가상환경 생성
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. 패키지 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env
# .env 파일에 실제 키 입력

# 4. DART API 키 테스트
python -c "import dart_fss as dart; dart.set_api_key('YOUR_KEY'); print('DART OK')"

# 5. pykrx 테스트
python -c "from pykrx import stock; print(stock.get_market_ticker_name('005930'))"
```

---

## API 키 발급 체크리스트

| API | 발급처 | 비고 |
|-----|--------|------|
| OpenAI | platform.openai.com | 필수 |
| DART | opendart.fss.or.kr | 필수, 무료 |
| Naver 검색 | developers.naver.com/apps | 선택, 무료 25,000콜/일 |

---

## 리스크 및 주의사항

| 리스크 | 대응 |
|--------|------|
| DART API 일일 한도 초과 | VectorStore 캐싱으로 재호출 최소화 |
| pykrx 과거 데이터 없음 | 상장 전 / 상장폐지 연도는 빈 dict로 처리 |
| LLM 환각으로 잘못된 날짜 추출 | 프롬프트에 `null 반환 강제`, 날짜 형식 검증 코드 추가 |
| 50종목 × 10년 실행 시간 | 단계별 실행 / 체크포인트 재개 활용 |
| 보통주/우선주 코드 혼재 | `normalize_input`에서 `is_preferred` 플래그 처리 |

---

## 다음 단계 (MVP 이후)

MVP 완료 후 아래 순서로 기능을 추가한다.

1. **향후 12개월 예상 배당 추정** — `FORWARD_ESTIMATE_PROMPT` 활용, `estimate_forward_dividend` 노드 추가
2. **자사주 소각 데이터 수집** — 별도 서브그래프로 분리
3. **병렬 처리** — `asyncio` 또는 `ThreadPoolExecutor`로 종목-연도 단위 병렬화
4. **배당 투자 스크리닝** — 신뢰도 점수 ≥ 0.8 종목 필터링 기능
