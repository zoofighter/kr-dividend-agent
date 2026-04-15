---
tags:
  - 구현
  - 진행상황
  - LangGraph
created: 2026-04-15
related:
  - "[[implementation-plan]]"
  - "[[dart-api-findings]]"
---

# 구현 진행 상황

> [!success] 현재 상태
> Phase 0~5 구현 완료. 삼성전자 단건 엔드-투-엔드 테스트 통과.
> 50종목 × 10년 배치 실행 및 엑셀 출력 검증 단계.

---

## 완료된 작업

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 0 | 프로젝트 뼈대 · 환경 설정 | ✅ 완료 |
| Phase 1 | 종목 정규화 · 날짜 검증 | ✅ 완료 |
| Phase 2 | DART API 배당 데이터 수집 | ✅ 완료 |
| Phase 3 | 웹 검색 보완 (Naver / DuckDuckGo) | ✅ 완료 |
| Phase 4 | 검증 루프 · LangGraph 조립 | ✅ 완료 |
| Phase 5 | 엑셀 출력 · 배치 실행 뼈대 | ✅ 완료 |
| Phase 6 | 50종목 배치 실행 · 품질 확인 | 🔲 미완료 |

---

## 파일 구조

```
src/
├── config.py          환경변수 로드, 상수 정의
├── state.py           DividendAgentState TypedDict
├── prompts.py         LLM 프롬프트 5종 (PROMPT_VERSION=v1.0)
├── graph.py           LangGraph 그래프 조립 및 컴파일
├── main.py            배치 실행 진입점
├── tools/
│   ├── dart_rag.py    DART API 직접 호출 (alotMatter + document.xml)
│   ├── web_search.py  Naver API + DuckDuckGo 폴백
│   ├── validator.py   배당락일 규칙 검증 (XKRX 캘린더)
│   └── excel_tool.py  3시트 엑셀 출력
└── nodes/
    ├── normalize.py       normalize_input
    ├── dart_node.py       search_dart_rag, extract_dividend_from_dart
    ├── web_node.py        search_web
    ├── validate_node.py   validate_result, build_retry_query
    ├── metrics_node.py    calculate_metrics
    └── save_node.py       save_result, mark_manual_review
```

---

## LangGraph 흐름

```
normalize_input
  → search_dart_rag
    → extract_dividend_from_dart
      → search_web
        → validate_result
            ├─ [valid]         → calculate_metrics → save_result → END
            ├─ [retry]         → build_retry_query → search_dart_rag (루프)
            └─ [manual_review] → mark_manual_review → END
```

---

## 주요 설계 변경 이력

### pykrx 제거 → DART 2소스 구조

| | 초기 설계 | 현재 구현 |
|--|---------|---------|
| 1차 소스 | DART RAG (임베딩) | DART 구조화 API |
| 2차 소스 | pykrx 배당 이력 | 웹 검색 (Naver/DuckDuckGo) |
| LLM 역할 | DART 문서 파싱 필수 | 웹 스니펫 보완 시만 사용 |

**이유:**
- pykrx API 불안정 (빈 응답)
- DART `alotMatter.json`이 구조화된 배당 데이터 직접 제공

### DART RAG → DART 직접 API

- `alotMatter.json`: 배당금·수익률·성향·결산일 → **항상 수집 가능**
- `document.xml` 파싱: 배당기준일·지급일 → **대부분 수집 가능**
- FAISS·임베딩·sentence-transformers 불필요

### LLM 의존성 최소화

```
DART 데이터 추출  → 직접 파싱 (LLM 불필요)
웹 스니펫 추출   → Ollama 사용, DART 완전하면 스킵
재검색 쿼리 생성 → Ollama 사용, 실패 시 기본 쿼리 폴백
수동검토 판단근거 → Ollama 사용, 선택적 (없어도 동작)
```

---

## 엔드-투-엔드 테스트 결과 (삼성전자 2023)

```
normalize_input        ticker=005930, name=삼성전자
search_dart_rag        DART API 호출 성공
extract_dividend_from_dart  직접 파싱 성공 (LLM 불필요)
search_web             DART 완전 → 스킵
validate_result        status=valid, confidence=1.0, issues=0건
calculate_metrics      배당금=1444원, 수익률=1.9%, 기준일=2023-12-31
save_result            저장 완료
```

---

## 배당 날짜 수집 가능 여부

| 항목 | 수집 방법 | 가능 여부 |
|------|----------|----------|
| 배당금 | DART `alotMatter.json` | ✅ 항상 |
| 배당수익률 | DART `alotMatter.json` | ✅ 항상 |
| 결산일 (기준일) | DART `stlm_dt` | ✅ 항상 |
| 배당지급일 | 공시 원문 HTML 파싱 | ✅ 대부분 |
| 배당락일 | validator (기준일 - 1 영업일) | ✅ 계산 |

> [!warning] 분기 배당 종목 (예: 삼성전자)
> 12월 31일 기준 공시에서 지급일이 **미정**으로 표기됨.
> 상세 내용은 [[dart-api-findings]] 참고.

---

## 검증 정책

| 항목 | 기준 |
|------|------|
| 배당금 허용 오차 | ±10원 |
| 배당락일 규칙 | 기준일 - 1 영업일 (XKRX 캘린더) |
| 최대 재시도 | 2회 (초과 시 manual_review) |
| 신뢰도 점수 | DART 완전 = 1.0, 웹 단독 보완 = -0.1 페널티 |

---

## 환경 설정

```env
DART_API_KEY=...          # 필수
NAVER_CLIENT_ID=...       # 선택 (없으면 DuckDuckGo)
NAVER_CLIENT_SECRET=...   # 선택
LOCAL_LLM_MODEL=llama3.2  # Ollama 모델
LOCAL_LLM_BASE_URL=http://localhost:11434
```

> [!tip] Ollama 없이도 동작
> DART 데이터가 완전한 종목은 LLM 없이 valid 판정.
> Ollama는 웹 검색 보완·재검색 쿼리 생성 시에만 필요.

---

## 다음 단계 (Phase 6)

- [ ] 50종목 × 10년 배치 실행
- [ ] 엑셀 출력 3시트 확인
- [ ] 수동 검토 비율 확인 (목표: manual_review < 20%)
- [ ] 배당락일 계산 오류 케이스 점검
- [ ] DART 데이터 없는 연도(상장 전) 처리 확인

---

## GitHub

저장소: `zoofighter/kr-dividend-agent`
최신 커밋: `5c40347` feat: implement Phase 3-4 web search, validation loop, LangGraph assembly
