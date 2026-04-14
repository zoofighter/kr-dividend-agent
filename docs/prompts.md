---
tags:
  - LangGraph
  - 프롬프트
  - Prompt
  - 배당
created: 2026-04-12
related:
  - "[[requirements]]"
  - "[[nodes]]"
  - "[[tools]]"
---

# 배당 데이터 수집 에이전트 — 프롬프트 정의서

> [!warning] 프롬프트 품질이 데이터 정확도를 결정한다.
> 이 문서의 프롬프트는 코드에서 상수로 관리하고, 실험 결과에 따라 버전 관리한다.

---

## 프롬프트 목록

| # | 상수명 | 사용 노드 | 역할 |
|---|--------|----------|------|
| 1 | `DART_EXTRACT_PROMPT` | `extract_dividend_from_dart` | DART 공시 → 배당 필드 구조화 추출 |
| 2 | `WEB_EXTRACT_PROMPT` | `search_web` | 웹 검색 스니펫 → 배당 필드 추출 |
| 3 | `RETRY_QUERY_PROMPT` | `build_retry_query` | 불일치 원인 → 재검색 쿼리 생성 |
| 4 | `VALIDATION_JUDGE_PROMPT` | `validate_result` | 충돌 내용 → 최종 판단 근거 생성 |
| 5 | `FORWARD_ESTIMATE_PROMPT` | `estimate_forward_dividend` | 과거 이력 → 향후 12개월 배당 예측 |

---

## 공통 설계 원칙

모든 프롬프트가 따르는 4가지 원칙:

| 원칙 | 내용 | 이유 |
|------|------|------|
| **외부 지식 금지** | 주어진 문서에서만 추출 | LLM 환각으로 인한 오데이터 방지 |
| **추정 금지** | 확인 불가 필드는 반드시 `null` | 틀린 데이터보다 빈 데이터가 낫다 |
| **evidence 필수** | 각 값의 근거 문장 함께 반환 | 검증 로그 및 수동 확인 근거 확보 |
| **JSON 강제** | 구조화 출력으로 파싱 오류 최소화 | 후처리 코드 단순화 |

---

## 프롬프트 1 — `DART_EXTRACT_PROMPT`

### 사용 위치
- 노드: `extract_dividend_from_dart`
- 입력 변수: `{dart_chunks}`, `{company_name}`, `{year}`

### 프롬프트

```python
DART_EXTRACT_PROMPT = """\
[역할]
당신은 한국 증시 배당 공시 전문 데이터 추출기다.
주어진 DART 공시 문서에서 배당 관련 수치를 정확하게 추출한다.

[추출 대상 종목]
- 회사명: {company_name}
- 대상 연도: {year}년

[추출 대상 필드]
- dividend_amount   : 1주당 현금 배당금 (원, 숫자만 — 단위 제외)
- record_date       : 배당기준일 (주주 명부 확정 기준일, YYYY-MM-DD)
- ex_dividend_date  : 배당락일 (record_date - 1 영업일, YYYY-MM-DD)
- payment_date      : 실제 배당금 지급일 (YYYY-MM-DD)
- dividend_status   : 확정 | 예정 | 미확정

[필드 정의 주의]
- 배당기준일(record_date): 배당을 받을 주주를 확정하는 날짜
- 배당락일(ex_dividend_date): 이 날 이후 매수하면 배당 미수령 — 배당기준일보다 1 영업일 앞선 날
- 배당지급일(payment_date): 실제로 계좌에 배당금이 입금되는 날

[우선순위 규칙]
1. 보통주와 우선주가 혼재하면 보통주 기준으로 추출한다
2. 결산배당과 중간배당이 혼재하면 결산배당을 우선 추출한다
3. 금액 단위가 '원'이 아닌 경우(천원, 백만원) 원 단위로 변환한다

[금지 사항]
- 문서에 없는 값을 추정하거나 보간하지 않는다
- 찾을 수 없는 필드는 반드시 null로 반환한다
- 외부 지식(인터넷, 상식)을 사용하지 않는다

[공시 문서]
{dart_chunks}

[출력 — JSON만 반환, 설명 없이]
{{
  "dividend_amount": <float | null>,
  "record_date": "<YYYY-MM-DD | null>",
  "ex_dividend_date": "<YYYY-MM-DD | null>",
  "payment_date": "<YYYY-MM-DD | null>",
  "dividend_status": "<확정 | 예정 | 미확정 | null>",
  "evidence": "<각 값의 근거가 된 원문 문장>"
}}
"""
```

### 파싱 코드

```python
from langchain_core.output_parsers import JsonOutputParser

chain = DART_EXTRACT_PROMPT | llm | JsonOutputParser()
result = chain.invoke({
    "dart_chunks": "\n---\n".join(c["content"] for c in state["dart_chunks"]),
    "company_name": state["company_name"],
    "year": state["year"],
})
```

### 예상 출력 예시

```json
{
  "dividend_amount": 1444,
  "record_date": "2025-12-31",
  "ex_dividend_date": "2025-12-27",
  "payment_date": "2026-04-17",
  "dividend_status": "확정",
  "evidence": "2025년 결산배당으로 보통주 1주당 금 1,444원을 현금 배당하기로 결의함. 배당기준일: 2025.12.31, 배당지급 예정일: 2026.04.17"
}
```

---

## 프롬프트 2 — `WEB_EXTRACT_PROMPT`

### 사용 위치
- 노드: `search_web`
- 입력 변수: `{snippets}`, `{company_name}`, `{year}`

### 프롬프트

```python
WEB_EXTRACT_PROMPT = """\
[역할]
당신은 뉴스 및 웹 문서에서 배당 정보를 추출하는 보조 추출기다.

[추출 대상 종목]
- 회사명: {company_name}
- 대상 연도: {year}년

[추출 대상 필드]
- dividend_amount  : 1주당 현금 배당금 (원)
- record_date      : 배당기준일 (YYYY-MM-DD)
- ex_dividend_date : 배당락일 (YYYY-MM-DD)
- payment_date     : 배당지급일 (YYYY-MM-DD)
- dividend_status  : 확정 | 예정 | 미확정

[주의 사항]
- 웹 문서는 비공식 소스다 — 정확하지 않을 수 있음
- 명확히 언급된 수치만 추출한다 (모호한 내용은 null)
- 찾을 수 없는 필드는 null로 반환한다
- 출처 URL을 evidence_url에 포함한다

[웹 검색 결과]
{snippets}

[출력 — JSON만 반환]
{{
  "dividend_amount": <float | null>,
  "record_date": "<YYYY-MM-DD | null>",
  "ex_dividend_date": "<YYYY-MM-DD | null>",
  "payment_date": "<YYYY-MM-DD | null>",
  "dividend_status": "<확정 | 예정 | 미확정 | null>",
  "evidence": "<근거 문장>",
  "evidence_url": "<출처 URL | null>"
}}
"""
```

### 스니펫 포맷 변환 코드

```python
def format_snippets(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] 제목: {r['title']}")
        lines.append(f"    내용: {r['description']}")
        lines.append(f"    URL: {r['url']}")
    return "\n".join(lines)
```

---

## 프롬프트 3 — `RETRY_QUERY_PROMPT`

### 사용 위치
- 노드: `build_retry_query`
- 입력 변수: `{company}`, `{year}`, `{validation_reason}`, `{extracted_from_dart}`, `{extracted_from_pykrx}`

### 프롬프트

```python
RETRY_QUERY_PROMPT = """\
[상황]
{company} {year}년 배당 데이터 수집 중 다음 문제가 발생했다:
{validation_reason}

[현재 수집된 값]
DART 공시: {extracted_from_dart}
pykrx 이력: {extracted_from_pykrx}

[지시]
위 불일치를 해소할 수 있는 DART 공시 검색 쿼리를 1개 생성하라.

[쿼리 작성 규칙]
- 회사명과 연도를 반드시 포함한다
- 불일치 원인과 관련된 키워드를 포함한다
- 20자 이내로 작성한다
- 검색어만 반환한다 (설명 없이)

[불일치 유형별 키워드 힌트]
- 배당금 불일치 → "주당배당금", "결산배당", "사업보고서"
- 배당락일 불일치 → "배당기준일", "배당락일", "주주확정"
- 배당지급일 불일치 → "배당지급일", "배당금 지급"
- 미확인 필드 → "배당결정", "이사회결의"
"""
```

### 출력 파싱

```python
# 쿼리 문자열만 반환되므로 단순 strip()
chain = RETRY_QUERY_PROMPT | llm
result = chain.invoke({...})
retry_query = result.content.strip().strip('"').strip("'")
```

### 쿼리 생성 예시

| 불일치 원인 | 생성 쿼리 예시 |
|------------|--------------|
| 배당금 불일치 | `삼성전자 2024 주당배당금 결산` |
| 배당락일 불일치 | `삼성전자 2024 배당기준일 공시` |
| 배당지급일 없음 | `삼성전자 2024 배당금 지급 이사회` |

---

## 프롬프트 4 — `VALIDATION_JUDGE_PROMPT`

### 사용 위치
- 노드: `validate_result` — `manual_review` 판정 시 최종 판단 근거 생성에 사용
- 입력 변수: `{company}`, `{year}`, `{issues}`, `{dart}`, `{pykrx}`, `{web}`

### 프롬프트

```python
VALIDATION_JUDGE_PROMPT = """\
[역할]
당신은 한국 증시 배당 데이터 검증 전문가다.

[상황]
{company} {year}년 배당 데이터가 여러 소스 간 불일치로 자동 확정에 실패했다.
수동 검토 담당자가 참고할 수 있도록 판단 근거를 작성해야 한다.

[발견된 문제]
{issues}

[소스별 수집값]
- DART 공시: {dart}
- pykrx 이력: {pykrx}
- 웹 검색: {web}

[작성 지시]
1. 불일치의 가능한 원인을 2~3가지 추론한다
2. 수동 확인 시 가장 먼저 확인해야 할 항목을 제시한다
3. 3문장 이내로 간결하게 작성한다
4. 확신이 없으면 "불명확"이라고 명시한다
"""
```

### 출력 예시

```
배당금 불일치(DART 1,444원 vs pykrx 1,200원)는 중간배당 포함 여부 차이로 추정됩니다.
DART 사업보고서의 '주당 현금배당금' 항목을 직접 확인하고, 결산배당과 중간배당 합산 여부를 구분하세요.
pykrx DIV 수치는 당일 종가 기반 역산값이므로 오차가 발생할 수 있습니다.
```

---

## 프롬프트 5 — `FORWARD_ESTIMATE_PROMPT`

### 사용 위치
- 노드: `estimate_forward_dividend` (향후 추가)
- 입력 변수: `{company_name}`, `{ticker}`, `{current_date}`, `{history}`, `{current_price}`

### 프롬프트

```python
FORWARD_ESTIMATE_PROMPT = """\
[역할]
당신은 한국 증시 배당 투자 분석가다.
과거 배당 이력을 바탕으로 향후 12개월 예상 배당을 추정한다.

[분석 대상]
- 종목: {company_name} ({ticker})
- 기준일: {current_date}
- 현재 주가: {current_price}원

[과거 배당 이력 (최근 10년)]
{history}

[추정 지시]
1. 최근 3년 배당금 추세(증가/감소/유지)를 파악한다
2. 이상치(급증/급감)가 있으면 제외하고 판단한다
3. 향후 12개월 예상 배당금을 추정한다
4. 예상 배당수익률을 현재 주가 기준으로 계산한다

[반드시 포함할 항목]
- 추정 근거 (어떤 추세를 사용했는지)
- 불확실성 요인 (배당 변동 가능성이 있다면 명시)

[출력 — JSON만 반환]
{{
  "projected_dividend_amount": <float | null>,
  "projected_dividend_yield": <float | null>,
  "estimate_method": "<추정 방법 설명>",
  "confidence": "<높음 | 보통 | 낮음>",
  "risk_factors": "<불확실성 요인, 없으면 null>",
  "dividend_status": "예상"
}}

[신뢰도 기준]
- 높음: 최근 5년 이상 배당 지속, 증가 추세 명확
- 보통: 배당 지속이나 금액 변동 있음
- 낮음: 배당 이력 3년 미만, 불규칙 지급, 전년 배당 급감
"""
```

### 이력 데이터 포맷 변환 코드

```python
def format_history(history: dict) -> str:
    lines = ["연도 | 배당금(원) | 배당수익률(%)"]
    lines.append("-----|-----------|-------------")
    for year in sorted(history.keys()):
        h = history[year]
        amount = h.get("dividend_amount", "-")
        yld    = h.get("dividend_yield", "-")
        lines.append(f"{year} | {amount:,} | {yld}")
    return "\n".join(lines)
```

### 출력 예시

```json
{
  "projected_dividend_amount": 1550,
  "projected_dividend_yield": 2.5,
  "estimate_method": "최근 3년(2022-2024) 평균 1,430원 기준, 연 5% 성장률 적용",
  "confidence": "보통",
  "risk_factors": "2023년 배당금이 전년 대비 15% 감소한 이력 있음. 실적 악화 시 삭감 가능성 존재.",
  "dividend_status": "예상"
}
```

---

## 프롬프트 버전 관리

```python
# prompts.py — 모든 프롬프트를 한 파일에서 관리

PROMPT_VERSION = "v1.0"

DART_EXTRACT_PROMPT    = "..."  # 프롬프트 1
WEB_EXTRACT_PROMPT     = "..."  # 프롬프트 2
RETRY_QUERY_PROMPT     = "..."  # 프롬프트 3
VALIDATION_JUDGE_PROMPT = "..." # 프롬프트 4
FORWARD_ESTIMATE_PROMPT = "..." # 프롬프트 5
```

---

## 프롬프트 개선 체크리스트

프롬프트 수정 전 확인할 항목:

- [ ] `null` 반환 지시가 명확한가
- [ ] 보통주 / 결산배당 우선 규칙이 명시되어 있는가
- [ ] `evidence` 필드가 JSON 출력에 포함되어 있는가
- [ ] 날짜 형식(`YYYY-MM-DD`)이 명시되어 있는가
- [ ] JSON 이외의 텍스트 출력을 금지했는가
- [ ] 입력 변수(`{}`)가 코드와 일치하는가
