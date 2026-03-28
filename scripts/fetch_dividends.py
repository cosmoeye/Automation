#!/usr/bin/env python3
"""
배당금 데이터 수집 스크립트
입력: docs/dividend-tracker/holdings.json
출력: docs/dividend-tracker/data.json

세금:
  - 미국 주식: 15% (한-미 조세조약)
  - 한국 주식: 15.4% (배당소득세 14% + 지방소득세 1.4%)
"""

import json
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

import yfinance as yf
import pandas as pd

TAX_RATES = {"US": 0.15, "KR": 0.154}
FREQ_DAYS = {"monthly": 30, "quarterly": 91, "semi-annual": 182, "annual": 365}

BASE_DIR = Path(__file__).parent.parent
HOLDINGS_FILE = BASE_DIR / "docs" / "dividend-tracker" / "holdings.json"
OUTPUT_FILE = BASE_DIR / "docs" / "dividend-tracker" / "data.json"


def get_usd_krw_rate() -> float:
    """달러/원 환율 조회"""
    try:
        hist = yf.Ticker("USDKRW=X").history(period="5d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            print(f"  USD/KRW: {rate:,.0f}")
            return rate
    except Exception as e:
        print(f"  환율 조회 실패 ({e}), 기본값 1,320원 사용")
    return 1320.0


def detect_frequency(dividend_dates: list) -> str:
    """배당 지급 빈도 감지"""
    if len(dividend_dates) < 2:
        return "annual"

    sorted_dates = sorted(dividend_dates)
    intervals = [
        (sorted_dates[i] - sorted_dates[i - 1]).days
        for i in range(1, len(sorted_dates))
    ]
    avg = sum(intervals) / len(intervals)

    if avg < 45:
        return "monthly"
    elif avg < 120:
        return "quarterly"
    elif avg < 240:
        return "semi-annual"
    return "annual"


def project_dividends(history: list, frequency: str, months_ahead: int = 13) -> list:
    """향후 배당금 예측 (마지막 4회 평균 금액 사용)"""
    if not history:
        return []

    recent = sorted(history, key=lambda x: x["date"], reverse=True)[:4]
    avg_amount = sum(d["per_share"] for d in recent) / len(recent)
    interval = timedelta(days=FREQ_DAYS.get(frequency, 365))

    last_date = max(d["date"] for d in history)
    end_date = date.today() + timedelta(days=months_ahead * 30)

    projections = []
    next_date = last_date + interval
    while next_date <= end_date:
        projections.append({"date": next_date, "per_share": round(avg_amount, 6)})
        next_date += interval

    return projections


def process_stock(ticker_symbol: str, market: str, shares: float, usd_krw: float) -> dict:
    """단일 종목 배당 데이터 처리"""
    tax_rate = TAX_RATES.get(market, 0.154)
    is_usd = market == "US"

    ticker = yf.Ticker(ticker_symbol)

    # 종목명 조회
    try:
        info = ticker.fast_info
        name = getattr(info, "short_name", None) or ticker_symbol
    except Exception:
        name = ticker_symbol

    # 배당 이력 조회 (3년)
    try:
        raw_dividends = ticker.dividends
    except Exception as e:
        print(f"    배당 조회 실패: {e}")
        raw_dividends = pd.Series(dtype=float)

    if raw_dividends.empty:
        return {
            "name": name,
            "market": market,
            "shares": shares,
            "frequency": "unknown",
            "no_dividend": True,
            "dividends_history": [],
            "dividends_projected": [],
        }

    cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=3)
    raw_dividends = raw_dividends[raw_dividends.index >= cutoff]

    def build_dividend_row(dt: date, per_share: float) -> dict:
        total = per_share * shares
        after_tax = total * (1 - tax_rate)
        after_tax_krw = after_tax * usd_krw if is_usd else after_tax
        return {
            "date": dt.isoformat(),
            "per_share": round(per_share, 6),
            "total": round(total, 4),
            "after_tax": round(after_tax, 4),
            "after_tax_krw": round(after_tax_krw),
        }

    history = [
        build_dividend_row(ts.date(), float(amount))
        for ts, amount in raw_dividends.items()
    ]

    # 빈도 감지
    date_objects = [datetime.fromisoformat(d["date"]).date() for d in history]
    frequency = detect_frequency(date_objects)

    # 미래 예측
    history_for_proj = [
        {"date": datetime.fromisoformat(d["date"]).date(), "per_share": d["per_share"]}
        for d in history
    ]
    raw_projected = project_dividends(history_for_proj, frequency)

    projected = []
    for p in raw_projected:
        row = build_dividend_row(p["date"], p["per_share"])
        row["projected"] = True
        projected.append(row)

    return {
        "name": name,
        "market": market,
        "shares": shares,
        "frequency": frequency,
        "no_dividend": False,
        "dividends_history": history,
        "dividends_projected": projected,
    }


def build_monthly_summary(stocks: dict) -> dict:
    """월별 배당 합계 계산"""
    monthly: dict = {}

    for data in stocks.values():
        for div in data.get("dividends_history", []):
            m = div["date"][:7]
            bucket = monthly.setdefault(m, {"actual_krw": 0, "projected_krw": 0})
            bucket["actual_krw"] += div["after_tax_krw"]

        for div in data.get("dividends_projected", []):
            m = div["date"][:7]
            bucket = monthly.setdefault(m, {"actual_krw": 0, "projected_krw": 0})
            bucket["projected_krw"] += div["after_tax_krw"]

    for m in monthly:
        monthly[m]["actual_krw"] = round(monthly[m]["actual_krw"])
        monthly[m]["projected_krw"] = round(monthly[m]["projected_krw"])

    return monthly


def main():
    with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
        holdings_data = json.load(f)

    holdings = holdings_data.get("holdings", [])
    if not holdings:
        print("보유 종목이 없습니다. holdings.json을 확인하세요.")
        sys.exit(1)

    print(f"총 {len(holdings)}개 종목 처리 시작\n")

    print("환율 조회 중...")
    usd_krw = get_usd_krw_rate()

    stocks: dict = {}
    for h in holdings:
        ticker = h["ticker"]
        market = h["market"]
        shares = h["shares"]
        print(f"[{ticker}] {h.get('name', '')} — {market} / {shares:,}주")
        stocks[ticker] = process_stock(ticker, market, shares, usd_krw)
        freq = stocks[ticker].get("frequency", "?")
        hist_count = len(stocks[ticker].get("dividends_history", []))
        proj_count = len(stocks[ticker].get("dividends_projected", []))
        print(f"  → 빈도: {freq}, 이력: {hist_count}건, 예측: {proj_count}건")

    monthly_summary = build_monthly_summary(stocks)

    today = date.today()
    ytd_start = date(today.year, 1, 1).isoformat()

    ytd_total = sum(
        div["after_tax_krw"]
        for data in stocks.values()
        for div in data.get("dividends_history", [])
        if div["date"] >= ytd_start
    )

    year_str = str(today.year)
    expected_annual = ytd_total + sum(
        div["after_tax_krw"]
        for data in stocks.values()
        for div in data.get("dividends_projected", [])
        if div["date"].startswith(year_str)
    )

    this_month_key = today.strftime("%Y-%m")
    this_month_bucket = monthly_summary.get(this_month_key, {})
    this_month_total = (
        this_month_bucket.get("actual_krw", 0)
        + this_month_bucket.get("projected_krw", 0)
    )

    output = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "exchange_rate": {"USDKRW": round(usd_krw, 2)},
        "summary": {
            "ytd_total_krw": round(ytd_total),
            "expected_annual_krw": round(expected_annual),
            "this_month_krw": round(this_month_total),
            "total_tickers": len(holdings),
        },
        "stocks": stocks,
        "monthly_summary": monthly_summary,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 완료 → {OUTPUT_FILE.name}")
    print(f"   YTD 수령액:  {ytd_total:>12,.0f}원")
    print(f"   연간 예상:   {expected_annual:>12,.0f}원")
    print(f"   이번 달 예상: {this_month_total:>11,.0f}원")


if __name__ == "__main__":
    main()
