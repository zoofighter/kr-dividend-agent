# dividend-agent

한국 상장 종목 50개의 **과거 10년 배당 데이터**를 자동 수집·검증하는 LangGraph 기반 에이전트.

DART 공시(RAG) · pykrx · 웹 검색(Naver / DuckDuckGo) 세 소스를 교차 검증하고 결과를 `.xlsx`로 저장한다.

---

## 주요 기능

- **DART RAG**: 공시 문서를 청크로 분할·임베딩하여 배당 필드를 LLM으로 정밀 추출
- **pykrx 이력 수집**: 과거 10년 배당 수익률·배당금 역산 (2차 검증 소스)
- **웹 검색 보완**: Naver 검색 API → DuckDuckGo 폴백으로 미등재 최신 배당 정보 보완
- **LangGraph 검증 루프**: 소스 간 불일치 발생 시 자동 재검색(최대 2회) → 수동 검토 분류
- **엑셀 출력**: 배당 데이터 / 검증 로그 / 수동 확인 필요 목록 3개 시트 구성

---

## 에이전트 흐름

```
START
  └─ normalize_input          종목코드·종목명 표준화
       └─ search_dart_rag     DART 공시 → RAG 청크 추출 (재시도 시 재진입)
            └─ extract_dividend_from_dart  LLM 구조화 추출
                 └─ fetch_pykrx_history    pykrx 배당 이력
                      └─ search_web        웹 검색 보완
                           └─ validate_result  3소스 교차 검증
                                ├─ [valid]   → calculate_metrics → save_result → END
                                ├─ [retry]   → build_retry_query → search_dart_rag (루프)
                                └─ [manual]  → mark_manual_review → END
```

---

## 출력 파일

`dividend_result_YYYYMMDD.xlsx`

| 시트 | 내용 |
|------|------|
| 배당 데이터 | 종목코드, 종목명, 연도, 배당금, 배당수익률, 배당락일, 배당기준일, 배당지급일, 검증 상태, 신뢰도 점수 |
| 검증 로그 | 소스 간 충돌 내용, 재시도 횟수, 최종 판단 근거 |
| 수동 확인 필요 | 자동 검증 실패 항목 + 소스별 수집값 + 근거 URL |

---

## 기술 스택

| 구분 | 라이브러리 |
|------|-----------|
| 에이전트 흐름 | `langgraph` |
| LLM | `langchain-openai` |
| DART RAG | `dart-fss`, `langchain`, `faiss-cpu` |
| 과거 배당 이력 | `pykrx` |
| 웹 검색 (1순위) | `requests` + Naver 검색 API |
| 웹 검색 (폴백) | `duckduckgo-search` |
| 영업일 계산 | `pandas-market-calendars` |
| 엑셀 출력 | `openpyxl` |
| 체크포인트 | `langgraph.checkpoint.sqlite` |

---

## 설치

```bash
pip install \
  langgraph langchain langchain-openai langchain-community \
  dart-fss pykrx faiss-cpu \
  pandas-market-calendars duckduckgo-search \
  openpyxl beautifulsoup4 requests
```

---

## 환경변수

```env
OPENAI_API_KEY=sk-...
DART_API_KEY=...
NAVER_CLIENT_ID=...          # 선택 (없으면 DuckDuckGo 폴백)
NAVER_CLIENT_SECRET=...
```

---

## 실행

```python
from langgraph.checkpoint.sqlite import SqliteSaver

checkpointer = SqliteSaver.from_conn_string("checkpoint.db")
graph = builder.compile(checkpointer=checkpointer)

for ticker, company in stock_list:          # data/stock_list.xlsx 기준 50종목
    for year in range(2016, 2026):
        config = {"configurable": {"thread_id": f"{ticker}_{year}"}}
        graph.invoke(
            {"ticker": ticker, "company_name": company, "year": year, "max_retry": 2},
            config=config,
        )
```

---

## 검증 정책

| 항목 | 허용 기준 |
|------|----------|
| 배당금 불일치 | ±10원 이내 정합 |
| 배당락일 규칙 | 배당기준일 − 1 영업일 (한국거래소 기준) |
| 최대 재시도 | 2회 (초과 시 `재확인필요`로 분류) |
| 신뢰도 점수 | DART+pykrx 일치 = 1.0, 웹 단독 보완 = −0.1 페널티 |

---

## MVP 범위

- [x] 과거 10년 배당 이력 수집
- [x] DART 공시 RAG 검색
- [x] LangGraph 검증 루프
- [x] 웹 검색 보완 (Naver / DuckDuckGo)
- [x] 엑셀 3시트 출력
- [ ] 자사주 소각 데이터 (추후)
- [ ] 배당 추천·스크리닝 (추후)
