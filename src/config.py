"""
프로젝트 전역 설정 — 환경변수 로드 및 상수 정의
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── 수집 연도 범위 ──────────────────────────────────
START_YEAR = 2016
END_YEAR   = 2025

# ── 검증 정책 ───────────────────────────────────────
MAX_RETRY          = 2
AMOUNT_TOLERANCE   = 10   # 배당금 허용 오차 (원)

# ── DART RAG 설정 ───────────────────────────────────
CHUNK_SIZE    = 500
CHUNK_OVERLAP = 50
RAG_TOP_K     = 5

# ── 출력 경로 ───────────────────────────────────────
OUTPUT_DIR = "output"

# ── 로컬 LLM (Ollama) ──────────────────────────────
LOCAL_LLM_MODEL    = os.getenv("LOCAL_LLM_MODEL", "llama3.2")
LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434")

# ── 임베딩 모델 (HuggingFace sentence-transformers) ─
EMBED_MODEL = os.getenv(
    "EMBED_MODEL",
    "snunlp/KR-SBERT-V40K-klueNLI-augSTS",   # 한국어 SBERT
)

# ── DART API ────────────────────────────────────────
DART_API_KEY = os.getenv("DART_API_KEY", "")

# ── Naver 검색 API (선택) ───────────────────────────
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
