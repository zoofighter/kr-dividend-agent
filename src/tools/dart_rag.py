"""
DART RAG 도구

흐름:
  1. dart-fss로 해당 종목·연도 공시 문서 검색 (사업보고서 / 반기보고서)
  2. 문서 텍스트를 청크로 분할
  3. HuggingFace SBERT 임베딩 → FAISS VectorStore
  4. 쿼리로 관련 청크 top-k 반환
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── dart-fss 초기화 (모듈 최초 import 시 1회) ────────────────────
_corp_list = None


def _get_corp_list():
    global _corp_list
    if _corp_list is None:
        import dart_fss as dart
        from src.config import DART_API_KEY
        dart.set_api_key(DART_API_KEY)
        _corp_list = dart.get_corp_list()
        logger.info("DART 법인 목록 로드 완료")
    return _corp_list


# ── 임베딩 / VectorStore (모델은 최초 1회 로드) ─────────────────
_embeddings = None


def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        from src.config import EMBED_MODEL
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBED_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        logger.info("임베딩 모델 로드 완료: %s", EMBED_MODEL)
    return _embeddings


def search_dart_disclosure(
    company_name: str,
    year: int,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """
    DART 공시에서 배당 관련 청크를 RAG로 검색한다.

    Parameters
    ----------
    company_name : 종목명 (예: "삼성전자")
    year         : 대상 연도 (예: 2024)
    query        : 검색 쿼리 (예: "삼성전자 2024 배당")
    top_k        : 반환할 청크 수

    Returns
    -------
    list[dict]  [{"content": str, "source": str, "score": float}, ...]
    빈 리스트 반환 시 공시 없음 또는 오류
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    from src.config import CHUNK_SIZE, CHUNK_OVERLAP

    # 1. 공시 문서 수집
    raw_docs = _fetch_dart_docs(company_name, year)
    if not raw_docs:
        logger.warning("DART 공시 없음: %s %d년", company_name, year)
        return []

    # 2. 텍스트 청크 분할
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", ".", " ", ""],
    )
    documents: list[Document] = []
    for doc in raw_docs:
        chunks = splitter.split_text(doc["text"])
        for chunk in chunks:
            documents.append(
                Document(
                    page_content=chunk,
                    metadata={"source": doc["source"], "year": year},
                )
            )

    if not documents:
        return []

    # 3. FAISS VectorStore 생성 및 유사도 검색
    try:
        embeddings = _get_embeddings()
        vs = FAISS.from_documents(documents, embeddings)
        results = vs.similarity_search_with_score(query, k=min(top_k, len(documents)))
    except Exception as exc:
        logger.error("FAISS 검색 오류: %s", exc)
        return []

    return [
        {
            "content": doc.page_content,
            "source": doc.metadata.get("source", ""),
            "score": float(score),
        }
        for doc, score in results
    ]


def _fetch_dart_docs(company_name: str, year: int) -> list[dict]:
    """dart-fss로 공시 원문 텍스트를 수집한다."""
    try:
        corp_list = _get_corp_list()
        corps = corp_list.find_by_corp_name(company_name, exactly=True)
        if not corps:
            logger.warning("DART 법인 검색 실패: %s", company_name)
            return []

        corp = corps[0]
        bgn_de = f"{year}0101"
        end_de = f"{year}1231"

        # 사업보고서(A001) 우선, 없으면 반기보고서(A002)
        docs = []
        for pblntf_ty in ["A001", "A002"]:
            try:
                filings = corp.search_filings(
                    bgn_de=bgn_de,
                    end_de=end_de,
                    pblntf_ty=pblntf_ty,
                    page_count=3,
                )
                if filings and len(filings) > 0:
                    docs.extend(_extract_text_from_filings(filings, company_name, year))
                    if docs:
                        break   # 사업보고서에서 충분히 수집되면 반기보고서 생략
            except Exception as exc:
                logger.debug("공시 검색 오류 type=%s: %s", pblntf_ty, exc)

        return docs

    except Exception as exc:
        logger.error("DART 문서 수집 오류 %s %d: %s", company_name, year, exc)
        return []


def _extract_text_from_filings(filings, company_name: str, year: int) -> list[dict]:
    """공시 객체에서 텍스트를 추출한다."""
    docs = []
    for filing in filings[:2]:   # 최대 2건
        try:
            # dart-fss Report 객체의 텍스트 접근
            report = filing.to_dict() if hasattr(filing, "to_dict") else {}

            # 공시 제목과 기본 정보로 텍스트 구성
            title = getattr(filing, "report_nm", "") or report.get("report_nm", "")
            rcept_dt = getattr(filing, "rcept_dt", "") or report.get("rcept_dt", "")

            # 상세 문서 내용 시도
            text_parts = [f"[공시] {title} ({rcept_dt})"]

            try:
                # dart-fss의 문서 내용 접근
                if hasattr(filing, "pages"):
                    for page in filing.pages[:10]:
                        if hasattr(page, "to_dict"):
                            page_dict = page.to_dict()
                            if "text" in page_dict:
                                text_parts.append(page_dict["text"][:2000])
            except Exception:
                pass

            text = "\n".join(filter(None, text_parts))
            if text.strip():
                source_url = getattr(filing, "rcept_no", "")
                docs.append({
                    "text": text,
                    "source": f"DART:{source_url}",
                })
        except Exception as exc:
            logger.debug("공시 텍스트 추출 오류: %s", exc)

    return docs
