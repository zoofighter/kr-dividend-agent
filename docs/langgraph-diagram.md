---
tags:
  - LangGraph
  - 배당
  - 아키텍처
  - 다이어그램
created: 2026-04-12
related:
  - "[[requirements]]"
---

# 배당 데이터 수집 에이전트 — LangGraph 다이어그램

---

## 1. 전체 그래프 흐름

```mermaid
flowchart TD
    START([START]) --> A

    A["🔧 normalize_input\n종목코드 표준화\n회사명 정규화\n보통주/우선주 구분"]

    B["🔍 search_dart_rag\nDART 공시 문서 수집\n청크 분할 + 임베딩\n유사도 검색 → 관련 청크 반환"]

    C["📄 extract_dividend_from_dart\nLLM으로 배당 필드 구조화 추출\ndividend_amount, record_date\nex_dividend_date, payment_date\ndividend_status"]

    D["📊 fetch_pykrx_history\npykrx API 호출\n과거 10년 배당 이력 수집\n배당락일 기준 주가 수집"]

    W["🌐 search_web\nNaver 검색 API (1순위)\nDuckDuckGo (폴백)\nDART 미등재 최신 정보 보완"]

    E{"✅ validate_result\n소스 간 값 비교\n배당금 불일치 ±10원\n배당락일 규칙 검증\nretry_count 확인"}

    F["📈 calculate_metrics\n배당 수익률 계산\n신뢰도 점수 산정\n데이터 출처 정리"]

    G["💾 save_result\n엑셀 행 데이터 변환\n저장 버퍼에 추가"]

    H["🔄 build_retry_query\n불일치 원인 분석\n구체적 재검색 쿼리 생성\nretry_count + 1"]

    I["🚩 mark_manual_review\n수동 검토 항목 기록\n검증 로그 저장\n재확인필요 상태 저장"]

    END([END])

    A --> B
    B --> C
    C --> D
    D --> W
    W --> E

    E -->|"validation_status == valid"| F
    F --> G
    G --> END

    E -->|"validation_status == retry\n(retry_count < max_retry)"| H
    H -->|"retry_query 갱신"| B

    E -->|"validation_status == manual_review\n(retry_count >= max_retry)"| I
    I --> END
```

---

## 2. 검증 루프 상세

```mermaid
flowchart TD
    subgraph 검증루프["🔁 검증 루프 (최대 2회 재시도)"]
        V{"validate_result"}
        R["build_retry_query"]
        S["search_dart_rag"]

        V -->|"retry\n불일치 감지"| R
        R -->|"retry_query 생성\nretry_count +1"| S
        S -->|"새 청크 반환"| V
    end

    V -->|"valid"| NEXT["calculate_metrics →"]
    V -->|"manual_review\nretry_count ≥ 2"| END["mark_manual_review →"]
```

### 재시도 쿼리 전략

| 시도 | 조건 | 쿼리 전략 |
|------|------|----------|
| 1차 (기본) | retry_count = 0 | `"{회사명} {연도} 배당"` |
| 2차 (재시도 1) | 배당금 불일치 | `"{회사명} {연도} 주당배당금 결산 사업보고서 DART"` |
| 2차 (재시도 1) | 배당락일 불일치 | `"{회사명} {연도} 배당기준일 배당락일 공시"` |
| 3차 (재시도 2) | 기타 | `"{회사명} {연도} 배당 결정 공시"` |
| 이후 | retry_count ≥ max_retry | `manual_review` 종료 |

---

## 3. DART RAG 내부 흐름

```mermaid
flowchart LR
    subgraph RAG["🔍 search_dart_rag 내부"]
        DA["DART API\n공시 문서 수집\n(사업보고서/반기보고서)"]
        SP["텍스트 분할\nchunk_size=500\noverlap=50"]
        EM["임베딩 생성\nOpenAI / HuggingFace"]
        VS["VectorStore\nFAISS / Chroma\n인메모리"]
        SR["유사도 검색\n관련 청크 반환"]
    end

    Q["검색 쿼리\n(retry_query or 기본쿼리)"] --> SR
    DA --> SP --> EM --> VS --> SR
    SR --> OUT["dart_chunks\n관련 청크 리스트"]
```

---

## 4. State 흐름도

```mermaid
stateDiagram-v2
    [*] --> 입력초기화 : ticker, company_name, year, max_retry

    입력초기화 --> DART검색중 : normalize_input 완료
    DART검색중 --> 필드추출중 : dart_chunks 획득
    필드추출중 --> pykrx수집중 : extracted_from_dart 저장
    pykrx수집중 --> 검증중 : extracted_from_pykrx 저장

    검증중 --> 지표계산중 : validation_status = valid
    검증중 --> 재검색중 : validation_status = retry
    검증중 --> 수동검토 : validation_status = manual_review

    재검색중 --> DART검색중 : retry_query 갱신\nretry_count + 1

    지표계산중 --> 저장완료 : 배당수익률, 신뢰도 점수 계산
    저장완료 --> [*] : saved = true

    수동검토 --> [*] : 재확인필요 기록
```

---

## 5. 배치 실행 구조

```mermaid
flowchart TD
    subgraph 배치["🔁 배치 실행 (50개 종목 × 10년)"]
        direction TB
        SL["종목 리스트\n50개 ticker"]
        YL["연도 범위\n2016 ~ 2025"]

        SL --> LOOP
        YL --> LOOP

        LOOP["for ticker in stocks:\n  for year in years:"]
        LOOP --> INIT["initial_state 생성\n{ticker, company_name, year, max_retry=2}"]
        INIT --> GRAPH["LangGraph.invoke()\nthread_id = ticker_year\nSqliteSaver checkpoint"]
        GRAPH --> RES["result 수집"]
        RES --> LOOP
    end

    배치 --> EXCEL["save_to_excel()\ndividend_result_YYYYMMDD.xlsx\n  Sheet1: 배당 데이터\n  Sheet2: 검증 로그\n  Sheet3: 수동 확인 필요"]
```

---

## 6. 노드별 입출력 요약

```mermaid
flowchart LR
    subgraph N1["normalize_input"]
        I1["IN: ticker, company_name"] --> O1["OUT: 표준 ticker 6자리\n정규화된 company_name"]
    end

    subgraph N2["search_dart_rag"]
        I2["IN: company_name, year\nretry_query (재시도시)"] --> O2["OUT: dart_chunks[]\ndart_query"]
    end

    subgraph N3["extract_dividend_from_dart"]
        I3["IN: dart_chunks[]"] --> O3["OUT: extracted_from_dart\n{dividend_amount, record_date,\nex_dividend_date, payment_date,\ndividend_status, evidence}"]
    end

    subgraph N4["fetch_pykrx_history"]
        I4["IN: ticker, year"] --> O4["OUT: extracted_from_pykrx\n{dividend_amount, dividend_yield,\npykrx_history}"]
    end

    subgraph N5["validate_result"]
        I5["IN: extracted_from_dart\nextracted_from_pykrx\nretry_count, max_retry"] --> O5["OUT: validation_status\n(valid/retry/manual_review)\nvalidation_reason"]
    end

    subgraph N6["build_retry_query"]
        I6["IN: validation_reason\ncompany_name, year\nretry_count"] --> O6["OUT: retry_query\nretry_count + 1"]
    end

    subgraph N7["calculate_metrics"]
        I7["IN: extracted_from_dart\nextracted_from_pykrx"] --> O7["OUT: dividend_yield\nconfidence_score\nsources[]"]
    end

    subgraph N8["save_result"]
        I8["IN: 전체 state"] --> O8["OUT: saved = true\n엑셀 행 데이터 추가"]
    end

    subgraph N9["mark_manual_review"]
        I9["IN: validation_reason\nretry_count\nextracted_from_dart\nextracted_from_pykrx"] --> O9["OUT: 수동 검토 시트 항목\nvalidation_status = manual_review"]
    end
```

---

## 7. 라우팅 로직

```mermaid
flowchart TD
    V["validate_result 완료"] --> R{"route_after_validation\nvalidation_status?"}

    R -->|"valid"| CM["calculate_metrics"]
    R -->|"retry\n(retry_count < max_retry)"| BQ["build_retry_query"]
    R -->|"manual_review\n(retry_count >= max_retry)"| MR["mark_manual_review"]
```

---

## 8. 엑셀 출력 구조

```mermaid
flowchart LR
    subgraph XLSX["📁 dividend_result_YYYYMMDD.xlsx"]
        S1["Sheet 1: 배당 데이터 (메인)\n종목코드, 종목명, 연도\n배당금, 배당수익률\n배당락일, 배당기준일, 배당지급일\n배당예정 여부, 데이터 출처\n검증 상태, 신뢰도 점수"]
        S2["Sheet 2: 검증 로그\n종목코드, 연도\n충돌 내용\n재시도 횟수\n최종 판단 근거"]
        S3["Sheet 3: 수동 확인 필요 목록\n자동 확정 실패 항목\n근거 링크"]
    end

    save_result --> S1
    save_result --> S2
    mark_manual_review --> S3
```
