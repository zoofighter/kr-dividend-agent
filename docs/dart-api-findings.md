---
tags:
  - DART
  - API
  - 배당
  - 구현
created: 2026-04-15
related:
  - "[[implementation-plan]]"
  - "[[tools]]"
---

# DART API 조사 결과 — 배당 데이터 수집

> [!summary] 핵심 결론
> DART는 구조화된 배당 전용 API를 제공한다.
> RAG(임베딩) 없이 직접 API 호출만으로 배당금·날짜를 수집할 수 있다.

---

## 1. 사용 가능한 DART API

| API 엔드포인트 | 용도 | 필수 파라미터 |
|--------------|------|--------------|
| `alotMatter.json` | 배당금·수익률·성향 구조화 데이터 | `corp_code`, `bsns_year`, `reprt_code` |
| `list.json` | 공시 목록 조회 | `corp_code`, `bgn_de`, `end_de` |
| `document.xml` | 공시 원문 ZIP(HTML) 다운로드 | `rcept_no` |

### `alotMatter.json` 응답 필드

```json
[
  {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,444", "stlm_dt": "2023-12-31"},
  {"se": "현금배당수익률(%)",   "stock_knd": "보통주", "thstrm": "1.90"},
  {"se": "(연결)현금배당성향(%)",               "thstrm": "67.80"}
]
```

- `thstrm`: 당기 값
- `frmtrm`: 전기 값
- `lwfr`: 전전기 값
- `stlm_dt`: 결산일 (= 배당기준일, YYYY-MM-DD)
- `reprt_code`: `11011` = 사업보고서, `11012` = 반기보고서

---

## 2. 배당 날짜 수집 가능 여부

| 항목 | 수집 방법 | 가능 여부 | 비고 |
|------|----------|----------|------|
| 배당금 (주당) | `alotMatter.json` | ✅ 항상 | `주당 현금배당금(원)` 보통주 |
| 배당수익률 | `alotMatter.json` | ✅ 항상 | `현금배당수익률(%)` 보통주 |
| 배당성향 | `alotMatter.json` | ✅ 항상 | `(연결)현금배당성향(%)` |
| 결산일 (기준일) | `alotMatter.json` `stlm_dt` | ✅ 항상 | 대부분 `YYYY-12-31` |
| 배당기준일 | `현금ㆍ현물배당결정` 공시 원문 | ✅ 대부분 | 분기배당은 분기별 기준일 |
| 배당지급일 | `현금ㆍ현물배당결정` 공시 원문 | ⚠️ 조건부 | 아래 주의사항 참고 |
| 배당락일 | 기준일 - 1 영업일 (XKRX 계산) | ✅ 계산 | `validator.py` 사용 |

---

## 3. 배당지급일 수집 시 주의사항

### 연 1회 배당 종목 (일반)
- 12월 결산 → 다음 해 4~5월 지급
- `현금ㆍ현물배당결정` 공시에 기준일·지급일 모두 포함 ✅

### 분기 배당 종목 (예: 삼성전자)
- 연 4회 공시 (`1월`, `4월`, `7월`, `10월`)
- **문제**: 12월 31일 기준 공시(1월 공시)에서 지급일이 **미정** 으로 표기됨
- 지급일은 3월 주주총회 결의 후 별도 확정
- `alotMatter.json`의 `stlm_dt`로 결산일을 기준일로 사용하는 것이 가장 안정적

```
삼성전자 분기 배당 공시 패턴:
  1월 공시: 기준일 = 직전 12-31, 지급일 = 미정
  4월 공시: 기준일 = 3-31,       지급일 = 5-xx ← 이게 결산 최종 지급일
  7월 공시: 기준일 = 6-30,       지급일 = 8-xx
  10월 공시: 기준일 = 9-30,      지급일 = 11-xx
```

---

## 4. 실제 수집 결과 (삼성전자 검증)

| 연도 | 배당금 | 수익률 | 성향 | 결산일 |
|------|--------|--------|------|--------|
| 2022 | 1,444원 | 2.5% | 17.9% | 2022-12-31 |
| 2023 | 1,444원 | 1.9% | 67.8% | 2023-12-31 |
| 2024 | 1,446원 | 2.7% | 29.2% | 2024-12-31 |

---

## 5. 구현 전략 (최종 채택)

```
RAG(임베딩) 방식 ❌ — DART가 구조화 API를 제공하므로 불필요

채택된 방식:
  1. alotMatter.json     → 배당금·수익률·성향·결산일
  2. document.xml 파싱   → 배당기준일·지급일 (가능한 경우)
  3. validator.py 계산   → 배당락일 = 기준일 - 1 영업일 (XKRX)
  4. 웹 검색 보완        → 위에서 누락된 필드 (Naver / DuckDuckGo)
```

### 날짜 우선순위 로직

```python
# 배당기준일: 공시 원문 > alot stlm_dt
record_date = dates.get("record_date") or alot.get("record_date")

# 배당지급일: 공시 원문 > 웹 검색 > None
payment_date = dates.get("payment_date") or web.get("payment_date")

# 배당락일: validator 계산 (record_date 있으면 항상 산출 가능)
ex_dividend_date = calc_ex_date(record_date)   # validator.py
```

---

## 6. dart-fss 라이브러리 vs 직접 API 호출

| | dart-fss | 직접 requests |
|--|---------|--------------|
| 법인 코드 조회 | ✅ `corp_list.find_by_corp_name()` | ❌ 별도 구현 필요 |
| 배당 구조화 데이터 | ❌ 미지원 | ✅ `alotMatter.json` |
| 공시 원문 | ✅ (비교적 편함) | ✅ `document.xml` ZIP |
| **채택** | **법인코드 조회에만 사용** | **배당 데이터 수집에 사용** |

---

## 7. `search_dart_disclosure()` 반환 구조

```python
[{
    "content": "[DART 배당 데이터] 삼성전자 2023년\n주당 현금배당금(보통주): 1444.0원\n...",
    "source": "DART:20240312000736",
    "score": 1.0
}]
```

LangGraph 노드(`extract_dividend_from_dart`)에서 이 청크를 LLM에 전달하거나,
직접 파싱하여 `extracted_from_dart` State에 저장한다.
