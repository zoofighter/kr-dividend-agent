---
tags:
  - LangGraph
  - 노드설명
  - 배당
created: 2026-04-12
related:
  - "[[requirements]]"
  - "[[langgraph-diagram]]"
---

# 배당 데이터 수집 에이전트 — 노드 상세 설명

> [!summary] 노드 목록
> 총 10개 노드: `normalize_input` → `search_dart_rag` → `extract_dividend_from_dart` → `fetch_pykrx_history` → `search_web` → `validate_result` → (`build_retry_query` 루프 or `calculate_metrics` or `mark_manual_review`)

---

## 실행 순서 한눈에 보기

```
START
  └─ 1. normalize_input       입력 정규화
       └─ 2. search_dart_rag  DART 공시 검색 (재시도 시 재진입)
            └─ 3. extract_dividend_from_dart  LLM 필드 추출
                 └─ 4. fetch_pykrx_history    pykrx 이력 수집
                      └─ 5. search_web        웹 검색 보완
                           └─ 6. validate_result  3자 교차 검증
                                ├─ [valid]    → 7. calculate_metrics
                                │                  └─ 8. save_result → END
                                ├─ [retry]    → 9. build_retry_query → (2로 돌아감)
                                └─ [manual]   → 10. mark_manual_review → END
```

---

## 노드 1 — `normalize_input`

### 역할
종목코드·종목명을 표준화하여 이후 모든 노드가 일관된 식별자를 사용하도록 준비한다.

### 처리 내용

| 작업 | 설명 |
|------|------|
| 종목코드 표준화 | 숫자를 6자리 zero-padding (`5930` → `005930`) |
| 보통주/우선주 분리 | `005930`(보통주) vs `005935`(우선주) 구분 |
| 회사명 정규화 | DART API 검색에 적합한 공식 법인명으로 변환 |
| 시장 구분 | KOSPI / KOSDAQ 판별 |

### 입력 / 출력 State

```
IN   ticker (미정제), company_name (미정제), year
OUT  ticker (6자리), company_name (DART 검색용), market
```

### 코드 스켈레톤

```python
def normalize_input(state: DividendAgentState) -> DividendAgentState:
    ticker = str(state["ticker"]).zfill(6)          # 6자리 패딩
    company = resolve_company_name(ticker)           # pykrx 종목명 조회
    market = get_market(ticker)                      # KOSPI / KOSDAQ

    return {
        "ticker": ticker,
        "company_name": company,
        "market": market,
    }
```

### 주의 사항
- 우선주 코드를 받았을 경우 그대로 유지하되 `is_preferred=True` 플래그 세팅
- DART는 법인 단위로 검색하므로 보통주·우선주 모두 동일 `company_name` 사용

---

## 노드 2 — `search_dart_rag`

### 역할
DART 공시 문서를 수집하고 **RAG(Retrieval-Augmented Generation)** 로 관련 청크만 추출한다.
재시도 시 `retry_query`를 사용해 더 정밀한 검색을 수행한다.

### RAG 내부 파이프라인

```
DART API 공시 수집
    → 텍스트 청크 분할 (chunk_size=500, overlap=50)
    → 임베딩 생성 (OpenAI / HuggingFace)
    → VectorStore 저장 (FAISS / Chroma, 인메모리)
    → 쿼리 유사도 검색 → 관련 청크 반환
```

### 검색 대상 보고서 유형

| 코드 | 보고서 종류 | 우선순위 |
|------|------------|---------|
| `A` | 사업보고서 (연간) | ★★★ 최우선 |
| `F` | 반기보고서 | ★★ |
| `배당결정공시` | 이사회 배당 결정 공시 | ★★ (최신 연도) |

### 입력 / 출력 State

```
IN   company_name, year, retry_query (재시도 시)
OUT  dart_chunks (관련 청크 리스트), dart_query (사용된 쿼리)
```

### 코드 스켈레톤

```python
def search_dart_rag(state: DividendAgentState) -> DividendAgentState:
    query = state.get("retry_query") or \
            f"{state['company_name']} {state['year']} 배당"

    docs = dart_api.search(
        corp_name=state["company_name"],
        bgn_de=f"{state['year']}0101",
        end_de=f"{state['year']}1231",
        pblntf_ty=["A", "F"],
    )

    relevant_chunks = rag_retriever.get_relevant_documents(query, docs)

    return {"dart_chunks": relevant_chunks, "dart_query": query}
```

### 주의 사항
- DART API는 하루 트래픽 한도 있음 → 청크를 인메모리에 캐싱해서 재사용
- 재시도 시 동일 문서에서 다른 쿼리로 재검색 가능 (API 재호출 불필요)
- 공시 문서가 없으면 `dart_chunks = []` 반환 → 다음 노드에서 `null` 처리

---

## 노드 3 — `extract_dividend_from_dart`

### 역할
DART 청크를 LLM에 전달해 배당 관련 필드를 **구조화 JSON으로 추출**한다.
프롬프트 품질이 전체 데이터 정확도를 결정한다.

### 추출 대상 필드

| 필드 | 설명 | 타입 |
|------|------|------|
| `dividend_amount` | 주당 배당금 (원) | float |
| `record_date` | 배당기준일 — 주주 명부 확정 기준일 | YYYY-MM-DD |
| `ex_dividend_date` | 배당락일 — record_date - 1 영업일 | YYYY-MM-DD |
| `payment_date` | 배당지급일 | YYYY-MM-DD |
| `dividend_status` | 확정 / 예정 / 미확정 | str |
| `evidence` | 각 값의 출처 문장 | str |

### 프롬프트 핵심 규칙

1. **외부 지식 사용 금지** — 주어진 문서에서만 추출
2. **추정 금지** — 찾을 수 없으면 `null` 반환
3. **보통주 우선** — 보통주·우선주 혼재 시 보통주 기준
4. **결산배당 우선** — 중간배당과 결산배당 혼재 시 결산배당
5. **evidence 필수** — 각 값의 근거 문장을 함께 반환

### 입력 / 출력 State

```
IN   dart_chunks
OUT  extracted_from_dart {dividend_amount, record_date, ex_dividend_date,
                          payment_date, dividend_status, evidence}
```

### 코드 스켈레톤

```python
def extract_dividend_from_dart(state: DividendAgentState) -> DividendAgentState:
    chunks_text = "\n---\n".join(c["content"] for c in state["dart_chunks"])

    response = llm.invoke(EXTRACT_PROMPT.format(dart_chunks=chunks_text))
    extracted = parse_json(response.content)

    return {"extracted_from_dart": extracted}
```

### 주의 사항
- `dart_chunks`가 비어 있으면 `extracted_from_dart = {}` 반환 (LLM 호출 생략)
- 배당락일은 문서에 명시되지 않는 경우가 많음 → `null`로 두고 `validate_result`에서 계산

---

## 노드 4 — `fetch_pykrx_history`

### 역할
**pykrx 라이브러리**로 과거 10년 배당 이력을 빠르게 수집한다.
DART 공시와의 교차 검증용 2차 소스이며, 배당 수익률 계산에도 사용한다.

### 수집 데이터

| 항목 | pykrx 컬럼 | 설명 |
|------|-----------|------|
| 배당수익률 | `DIV` | % 단위 |
| 주가 (배당락일 기준) | `Close` | 배당금 역산에 사용 |
| BPS / PER / PBR | `BPS`, `PER`, `PBR` | 참고용 |

### 배당금 역산 공식

```
dividend_amount ≈ (주가 × DIV%) / 100
```

> DART 공시의 배당금과 ±10원 이내이면 정합으로 판단.

### 입력 / 출력 State

```
IN   ticker, year
OUT  pykrx_history {year: {dividend_amount, dividend_yield, close_price, ...}}
     extracted_from_pykrx {dividend_amount, dividend_yield}
```

### 코드 스켈레톤

```python
def fetch_pykrx_history(state: DividendAgentState) -> DividendAgentState:
    from pykrx import stock

    df = stock.get_market_fundamental(
        f"{state['year']}0101",
        f"{state['year']}1231",
        state["ticker"],
    )
    # DIV가 가장 높은 날 = 배당락일 근방으로 추정
    max_div_row = df[df["DIV"] == df["DIV"].max()].iloc[0]

    extracted = {
        "dividend_yield": max_div_row["DIV"],
        "dividend_amount": round(max_div_row["Close"] * max_div_row["DIV"] / 100),
    }

    return {
        "pykrx_history": df.to_dict(),
        "extracted_from_pykrx": extracted,
    }
```

### 주의 사항
- pykrx는 배당락일 전후로 DIV 값이 급등하는 패턴 → 연중 최대값 날짜를 배당락일 추정에 사용
- 배당금 역산값은 **참고값**이므로 DART 확정값 대비 검증 용도로만 사용

---

## 노드 5 — `search_web`

### 역할
DART 공시에서 데이터를 찾지 못했거나 소스 간 불일치가 발생했을 때 **웹 검색으로 보완**한다.
Naver 검색 API를 우선 사용하고, 실패 시 DuckDuckGo로 폴백한다.

### 검색 제공자 우선순위

```
1순위: Naver 검색 API (뉴스 + 웹 문서, JSON 응답)
  └─ 실패 (한도 초과 / 미설정) 시
2순위: DuckDuckGo (duckduckgo-search 라이브러리, 무료)
```

### 처리 흐름

```
쿼리 생성: "{회사명} {연도} 배당금 배당기준일"
    → 검색 실행 (5건)
    → 스니펫에서 배당 관련 키워드 정규식 필터링
    → 필터링된 스니펫을 LLM에 전달 → 필드 추출
    → extracted_from_web 저장
```

### 입력 / 출력 State

```
IN   company_name, year
OUT  web_search_results (원본 스니펫 리스트)
     web_search_provider ("naver" / "duckduckgo")
     extracted_from_web {dividend_amount, record_date, ...}
```

### 코드 스켈레톤

```python
def search_web(state: DividendAgentState) -> DividendAgentState:
    query = f"{state['company_name']} {state['year']} 배당금 배당기준일"
    provider = "naver"

    try:
        results = naver_search(query, display=5)
    except NaverAPIError:
        provider = "duckduckgo"
        results = list(ddgs.text(query, max_results=5))

    filtered = [r for r in results if _is_dividend_related(r["description"])]

    # 스니펫 → LLM 추출
    extracted = extract_from_snippets(filtered) if filtered else {}

    return {
        "web_search_results": filtered,
        "web_search_provider": provider,
        "extracted_from_web": extracted,
    }
```

### 활용 원칙 및 제약

| 원칙 | 내용 |
|------|------|
| 보완 소스만 | DART·pykrx 모두 `null`인 필드에만 적용 |
| 단독 확정 불가 | 웹 단독 값으로는 `valid` 판정 불가 |
| 신뢰도 페널티 | 웹 값을 사용한 필드에 `confidence_score -= 0.1` |
| 출처 URL 필수 | `evidence`에 URL 포함 |

---

## 노드 6 — `validate_result`

### 역할
DART · pykrx · 웹 검색 3개 소스의 값을 교차 비교하고, 결과에 따라 다음 경로를 결정하는 **핵심 라우팅 노드**다.

### 검증 항목

| 항목 | 허용 기준 | 실패 시 |
|------|----------|--------|
| 배당금 불일치 | ±10원 이내 | `retry` 또는 `manual_review` |
| 배당락일 불일치 | 소스 간 날짜 일치 | `retry` |
| 배당기준일 불일치 | 소스 간 날짜 일치 | `retry` |
| 날짜 규칙 위반 | 배당락일 = 배당기준일 - 1 영업일 | `retry` |

### 판정 로직

```
이슈 없음          → validation_status = "valid"
이슈 있음 + retry_count < max_retry(2)
                  → validation_status = "retry"
이슈 있음 + retry_count >= max_retry
                  → validation_status = "manual_review"
```

### 웹 검색 보완 규칙

- DART와 pykrx 모두 `null`인 필드 → `extracted_from_web` 값으로 채움
- 웹 단독으로 채운 필드는 `confidence_score -= 0.1`

### 입력 / 출력 State

```
IN   extracted_from_dart, extracted_from_pykrx, extracted_from_web
     retry_count, max_retry
OUT  validation_status ("valid" / "retry" / "manual_review")
     validation_reason (충돌 상세 문자열)
```

### 코드 스켈레톤

```python
def validate_result(state: DividendAgentState) -> DividendAgentState:
    dart  = state.get("extracted_from_dart", {})
    pykrx = state.get("extracted_from_pykrx", {})
    web   = state.get("extracted_from_web", {})
    issues = []

    # 1) 배당금 비교
    dart_amt, pykrx_amt = dart.get("dividend_amount"), pykrx.get("dividend_amount")
    if dart_amt and pykrx_amt and abs(dart_amt - pykrx_amt) > 10:
        web_note = f", web={web.get('dividend_amount')}" if web.get("dividend_amount") else ""
        issues.append(f"배당금 불일치: DART={dart_amt}, pykrx={pykrx_amt}{web_note}")

    # 2) 배당락일 규칙 검증
    if dart.get("record_date") and dart.get("ex_dividend_date"):
        if not _validate_ex_date_rule(dart["record_date"], dart["ex_dividend_date"]):
            issues.append("배당락일이 배당기준일 -1 영업일 규칙에 맞지 않음")

    if not issues:
        return {"validation_status": "valid", "validation_reason": ""}

    if state.get("retry_count", 0) >= state.get("max_retry", 2):
        return {"validation_status": "manual_review",
                "validation_reason": "; ".join(issues)}

    return {"validation_status": "retry", "validation_reason": "; ".join(issues)}
```

---

## 노드 7 — `build_retry_query`

### 역할
`validate_result`에서 발견된 불일치 원인을 분석하여 **더 구체적인 DART 재검색 쿼리**를 생성한다.
이 노드를 거쳐 `search_dart_rag`로 돌아가는 것이 검증 루프의 핵심이다.

### 쿼리 전략

| 불일치 원인 | 생성 쿼리 |
|------------|----------|
| 배당금 불일치 | `"{회사명} {연도} 주당배당금 결산 사업보고서 DART"` |
| 배당락일 불일치 | `"{회사명} {연도} 배당기준일 배당락일 공시"` |
| 기타 | `"{회사명} {연도} 배당 결정 공시"` |

### 재시도 횟수별 쿼리 변화

| 시도 | 쿼리 |
|------|------|
| 1차 (기본) | `"{회사명} {연도} 배당"` |
| 2차 (retry 1) | 불일치 원인 반영 구체 쿼리 |
| 3차 (retry 2) | 보고서 종류 명시 쿼리 |
| 이후 | `manual_review` 종료 |

### 입력 / 출력 State

```
IN   validation_reason, company_name, year, retry_count
OUT  retry_query (새 검색 쿼리)
     retry_count (+ 1)
```

### 코드 스켈레톤

```python
def build_retry_query(state: DividendAgentState) -> DividendAgentState:
    reason  = state.get("validation_reason", "")
    company = state["company_name"]
    year    = state["year"]

    if "배당금 불일치" in reason:
        query = f"{company} {year} 주당배당금 결산 사업보고서 DART"
    elif "배당락일" in reason:
        query = f"{company} {year} 배당기준일 배당락일 공시"
    else:
        query = f"{company} {year} 배당 결정 공시"

    return {
        "retry_query": query,
        "retry_count": state.get("retry_count", 0) + 1,
    }
```

---

## 노드 8 — `calculate_metrics`

### 역할
검증을 통과한 데이터로 **가공 지표를 계산**하고 최종 신뢰도 점수를 산정한다.

### 계산 항목

| 항목 | 계산 방법 |
|------|----------|
| `dividend_yield` | `dividend_amount / close_price * 100` |
| `confidence_score` | 기본 1.0에서 아래 규칙으로 차감 |

### 신뢰도 점수 산정 규칙

| 조건 | 점수 |
|------|------|
| DART + pykrx 모두 일치 | **1.0** |
| DART만 있고 pykrx 없음 | 0.8 |
| pykrx만 있고 DART 없음 | 0.7 |
| 웹 검색으로 보완한 필드 존재 | -0.1 페널티 |
| 재시도 1회 후 통과 | -0.05 페널티 |
| 재시도 2회 후 통과 | -0.1 페널티 |

### 입력 / 출력 State

```
IN   extracted_from_dart, extracted_from_pykrx, extracted_from_web
     pykrx_history, retry_count, web_search_provider
OUT  dividend_yield, confidence_score, sources (사용 소스 목록)
```

---

## 노드 9 — `save_result`

### 역할
최종 검증·계산된 데이터를 **엑셀 저장 버퍼**에 추가한다.

### 저장 내용 (Sheet 1 행 데이터)

| 컬럼 | 값 |
|------|-----|
| 종목코드 | `ticker` |
| 종목명 | `company_name` |
| 연도 | `year` |
| 배당금 | `dividend_amount` |
| 배당수익률 | `dividend_yield` |
| 배당락일 | `ex_dividend_date` |
| 배당기준일 | `record_date` |
| 배당지급일 | `payment_date` |
| 배당예정 여부 | `dividend_status` |
| 데이터 출처 | `sources` |
| 검증 상태 | `"검증완료"` |
| 신뢰도 점수 | `confidence_score` |

### 입력 / 출력 State

```
IN   전체 state (최종 확정 필드 모두)
OUT  saved = True
```

---

## 노드 10 — `mark_manual_review`

### 역할
자동 검증에 실패한 항목을 **수동 검토 시트(Sheet 3)** 에 기록하고 종료한다.

### 저장 내용

| 컬럼 | 값 |
|------|-----|
| 종목코드 | `ticker` |
| 연도 | `year` |
| 충돌 내용 | `validation_reason` |
| 재시도 횟수 | `retry_count` |
| DART 수집값 | `extracted_from_dart` |
| pykrx 수집값 | `extracted_from_pykrx` |
| 웹 검색값 | `extracted_from_web` |
| 근거 URL | `web_search_results[].url` |

### 입력 / 출력 State

```
IN   ticker, year, validation_reason, retry_count
     extracted_from_dart, extracted_from_pykrx, extracted_from_web
OUT  validation_status = "manual_review" (Sheet 3 기록 완료)
```

### 최종 엑셀에서의 표현

- Sheet 1 검증 상태 컬럼: `"재확인필요"`
- Sheet 3 수동 확인 필요 목록에 별도 행으로 기록
- 신뢰도 점수: 미산정 (담당자 직접 확인 필요)

---

## 라우팅 함수 — `route_after_validation`

`validate_result` 완료 후 다음 노드를 결정하는 조건부 엣지 함수.

```python
def route_after_validation(state: DividendAgentState) -> str:
    status = state.get("validation_status")
    if status == "valid":
        return "calculate_metrics"
    if status == "retry":
        return "build_retry_query"
    return "mark_manual_review"
```

| `validation_status` | 다음 노드 |
|--------------------|----------|
| `"valid"` | `calculate_metrics` |
| `"retry"` | `build_retry_query` |
| `"manual_review"` | `mark_manual_review` |

---

## 노드별 State 변경 요약

```
normalize_input          ticker*, company_name*, market
search_dart_rag          dart_chunks, dart_query
extract_dividend_from_dart  extracted_from_dart
fetch_pykrx_history      pykrx_history, extracted_from_pykrx
search_web               web_search_results, web_search_provider, extracted_from_web
validate_result          validation_status, validation_reason
  ├─ build_retry_query   retry_query, retry_count++
  ├─ calculate_metrics   dividend_yield, confidence_score, sources
  │    └─ save_result    saved=True
  └─ mark_manual_review  (종료 기록)
```

`*` = 정규화 후 갱신
