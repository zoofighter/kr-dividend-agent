"""
Phase 1 검증 스크립트

테스트 항목:
  1. stock_list.xlsx 파싱
  2. normalize_input 노드
  3. pykrx_tool — 삼성전자 10년 배당 이력
  4. validator — 배당락일 규칙 검증
"""
import sys
sys.path.insert(0, ".")

from src.main import load_stock_list
from src.nodes.normalize import normalize_input
from src.tools.pykrx_tool import get_dividend_history
from src.tools.validator import validate_ex_dividend_date
from src.config import START_YEAR, END_YEAR


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


def test_pykrx():
    print("\n── 3. pykrx 배당 이력 (삼성전자 005930) ──")
    history = get_dividend_history("005930", START_YEAR, END_YEAR)
    assert len(history) > 0, "배당 데이터 없음 — 네트워크 확인 필요"
    for year in sorted(history.keys()):
        d = history[year]
        print(
            f"  {year}  배당금={d.get('dividend_amount')}원"
            f"  수익률={d.get('dividend_yield')}%"
            f"  배당락일={d.get('ex_dividend_date')}"
        )
    print(f"  → {len(history)}개 연도 수집 완료")


def test_validator():
    print("\n── 4. 배당락일 검증 ─────────────────────")
    cases = [
        # (record_date, ex_dividend_date, 기대 valid)
        ("2024-12-31", "2024-12-27", True),   # 2024년 삼성전자 — 정상
        ("2024-12-31", "2024-12-28", False),  # 의도적 오류
        ("2023-12-29", "2023-12-28", True),   # 정상
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
    test_pykrx()
    test_validator()
    print("\n✓ Phase 1 테스트 완료\n")
