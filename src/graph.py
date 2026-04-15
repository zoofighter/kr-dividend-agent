"""
LangGraph 그래프 조립

흐름:
  normalize_input
    → search_dart_rag
      → extract_dividend_from_dart
        → search_web
          → validate_result
            ├─ [valid]        → calculate_metrics → save_result → END
            ├─ [retry]        → build_retry_query → search_dart_rag (루프)
            └─ [manual_review] → mark_manual_review → END
"""
from __future__ import annotations

from langgraph.graph import StateGraph, END

from src.state import DividendAgentState
from src.nodes.normalize    import normalize_input
from src.nodes.dart_node    import search_dart_rag, extract_dividend_from_dart
from src.nodes.web_node     import search_web
from src.nodes.validate_node import validate_result, build_retry_query, route_after_validation
from src.nodes.metrics_node import calculate_metrics
from src.nodes.save_node    import save_result, mark_manual_review


def build_graph():
    """배당 데이터 수집 에이전트 그래프를 생성하고 컴파일한다."""
    builder = StateGraph(DividendAgentState)

    # ── 노드 등록 ─────────────────────────────────────────────
    builder.add_node("normalize_input",            normalize_input)
    builder.add_node("search_dart_rag",            search_dart_rag)
    builder.add_node("extract_dividend_from_dart", extract_dividend_from_dart)
    builder.add_node("search_web",                 search_web)
    builder.add_node("validate_result",            validate_result)
    builder.add_node("build_retry_query",          build_retry_query)
    builder.add_node("calculate_metrics",          calculate_metrics)
    builder.add_node("save_result",                save_result)
    builder.add_node("mark_manual_review",         mark_manual_review)

    # ── 엣지 (순차) ───────────────────────────────────────────
    builder.set_entry_point("normalize_input")
    builder.add_edge("normalize_input",            "search_dart_rag")
    builder.add_edge("search_dart_rag",            "extract_dividend_from_dart")
    builder.add_edge("extract_dividend_from_dart", "search_web")
    builder.add_edge("search_web",                 "validate_result")
    builder.add_edge("build_retry_query",          "search_dart_rag")   # retry 루프
    builder.add_edge("calculate_metrics",          "save_result")
    builder.add_edge("save_result",                END)
    builder.add_edge("mark_manual_review",         END)

    # ── 조건부 엣지 (validate_result → 분기) ─────────────────
    builder.add_conditional_edges(
        "validate_result",
        route_after_validation,
        {
            "valid":         "calculate_metrics",
            "retry":         "build_retry_query",
            "manual_review": "mark_manual_review",
        },
    )

    # ── SqliteSaver 체크포인트 ────────────────────────────────
    checkpointer = None
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        import sqlite3
        conn = sqlite3.connect("checkpoint.db", check_same_thread=False)
        checkpointer = SqliteSaver(conn)
    except Exception:
        try:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
        except Exception:
            checkpointer = None

    return builder.compile(checkpointer=checkpointer)


# 싱글톤 그래프 인스턴스
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
