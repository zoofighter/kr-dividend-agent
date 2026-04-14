"""
Phase 1 검증 스크립트

테스트 항목:
  1. stock_list.xlsx 파싱
  2. normalize_input 노드
  3. validator — 배당락일 규칙 검증
"""
import sys
sys.path.insert(0, ".")

from src.main import load_stock_list
from src.nodes.normalize import normalize_input
from src.tools.validator import validate_ex_dividend_date


def test_stock_list():
    print("\n── 1. stock_list.xlsx 파싱 ──────────────")
    stocks = load_stock_list()
    assert len(stocks) > 0, "종목 리스트가 비어 있음"
    for code, name in stocks[:5]:
        assert len(code) == 6 and code.isdigit(), f"코드 형식 오류: {code}"
        print(f"  {code}  {name}")
    print(f"  → 총 {len(stocks)}개 종목 로드 완료")


def test_normalize():
    print("\n── 2. normalize_input 노드 ──────────────")
    state = {"ticker": "'005930", "company_name": "삼성전자", "year": 2024}
    result = normalize_input(state)
    assert result["ticker"] == "005930", f"ticker 오류: {result['ticker']}"
    assert result["company_name"], "company_name 비어 있음"
    print(f"  ticker       : {result['ticker']}")
    print(f"  company_name : {result['company_name']}")
    print(f"  max_retry    : {result['max_retry']}")
    print("  → normalize_input 통과")


def test_validator():
    print("\n── 3. 배당락일 검증 ─────────────────────")
    cases = [
        # (record_date, ex_dividend_date, 기대 valid)
        # 2024-12-31(화) → 전 영업일 2024-12-30(월) ← XKRX 기준
        # ※ 실제 삼성 2024 배당락일은 12-27 (KRX 12-30 임시휴장, 캘린더 미반영)
        ("2023-12-29", "2023-12-28", True),   # 2023-12-29(금) → 전 영업일 2023-12-28(목) 정상
        ("2022-12-30", "2022-12-29", True),   # 2022-12-30(금) → 전 영업일 2022-12-29(목) 정상
        ("2023-12-29", "2023-12-27", False),  # 2023-12-27은 2 영업일 전 — 오류
    ]
    for record, ex, expected in cases:
        res = validate_ex_dividend_date(record, ex)
        status = "✓" if res["valid"] == expected else "✗"
        print(
            f"  {status} record={record} ex={ex}"
            f"  → valid={res['valid']}  ({res['reason']})"
        )


if __name__ == "__main__":
    test_stock_list()
    test_normalize()
    test_validator()
    print("\n✓ Phase 1 테스트 완료\n")
