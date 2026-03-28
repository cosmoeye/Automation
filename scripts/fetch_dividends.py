#!/usr/bin/env python3
"""
배당금 데이터 수집 스크립트
입력:
  docs/dividend-tracker/holdings.json       보유 종목
  docs/dividend-tracker/actual-dividends.json  실수령 기록 (있으면 우선 사용)
출력:
  docs/dividend-tracker/data.json

우선순위: 실수령 기록 > Yahoo Finance 이력 > 예측

세금 (Yahoo Finance 예측 전용, 실수령은 기록값 그대로):
  - 미국 주식: 15% (한-미 조세조약)
  - 한국 주식: 15.4% (배당소득세 14% + 지방소득세 1.4%)
"""

import json
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

import yfinance as yf
import pandas as pd

TAX_RATES  = {"US": 0.15, "KR": 0.154}
FREQ_DAYS  = {"monthly": 30, "quarterly": 91, "semi-annual": 182, "annual": 365}

BASE_DIR        = Path(__file__).parent.parent
HOLDINGS_FILE   = BASE_DIR / "docs" / "dividend-tracker" / "holdings.json"
ACTUAL_FILE     = BASE_DIR / "docs" / "dividend-tracker" / "actual-dividends.json"
OUTPUT_FILE     = BASE_DIR / "docs" / "dividend-tracker" / "data.json"


# ── 환율 ──────────────────────────────────────────────────────────────────────
def get_usd_krw_rate() -> float:
    try:
        hist = yf.Ticker("USDKRW=X").history(period="5d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            print(f"  USD/KRW: {rate:,.0f}")
            return rate
    except Exception as e:
        print(f"  환율 조회 실패 ({e}), 기본값 1,320원 사용")
    return 1320.0


# ── 실수령 기록 로드 ──────────────────────────────────────────────────────────
def load_actual_dividends() -> dict[str, list]:
    """ticker → [record, ...] 맵으로 반환"""
    if not ACTUAL_FILE.exists():
        return {}
    with open(ACTUAL_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    result: dict[str, list] = {}
    for rec in data.get("records", []):
        t = rec.get("ticker", "")
        if t:
            result.setdefault(t, []).append(rec)
    return result


# ── 빈도 감지 ──────────────────────────────────────────────────────────────────
def detect_frequency(dates: list) -> str:
    if len(dates) < 2:
        return "annual"
    sorted_d = sorted(dates)
    intervals = [(sorted_d[i] - sorted_d[i - 1]).days for i in range(1, len(sorted_d))]
    avg = sum(intervals) / len(intervals)
    if avg < 45:   return "monthly"
    if avg < 120:  return "quarterly"
    if avg < 240:  return "semi-annual"
    return "annual"


# ── 예측 ──────────────────────────────────────────────────────────────────────
def project_dividends(history: list, frequency: str, months_ahead: int = 13) -> list:
    """
    history: [{"date": date, "per_share": float}, ...]
    실수령 기록이 있으면 실수령 기준 KRW 금액으로 예측,
    없으면 Yahoo Finance per_share 기준.
    """
    if not history:
        return []

    recent = sorted(history, key=lambda x: x["date"], reverse=True)[:4]
    interval = timedelta(days=FREQ_DAYS.get(frequency, 365))
    last_date = max(d["date"] for d in history)
    end_date = date.today() + timedelta(days=months_ahead * 30)

    # 실수령 KRW 금액이 있으면 그 평균 사용 (더 정확)
    if any("amount_krw" in d for d in recent):
        avg_krw = sum(d.get("amount_krw", 0) for d in recent if d.get("amount_krw")) / max(
            sum(1 for d in recent if d.get("amount_krw")), 1
        )
        projections, next_date = [], last_date + interval
        while next_date <= end_date:
            projections.append({"date": next_date, "amount_krw": round(avg_krw), "per_share": None})
            next_date += interval
        return projections
    else:
        avg_per_share = sum(d["per_share"] for d in recent) / len(recent)
        projections, next_date = [], last_date + interval
        while next_date <= end_date:
            projections.append({"date": next_date, "per_share": round(avg_per_share, 6), "amount_krw": None})
            next_date += interval
        return projections


# ── 단일 종목 처리 ─────────────────────────────────────────────────────────────
def process_stock(
    ticker_symbol: str,
    market: str,
    shares: float,
    usd_krw: float,
    actual_records: list,
) -> dict:
    tax_rate = TAX_RATES.get(market, 0.154)
    is_usd = market == "US"

    # 종목명
    ticker_obj = yf.Ticker(ticker_symbol)
    try:
        name = getattr(ticker_obj.fast_info, "short_name", None) or ticker_symbol
    except Exception:
        name = ticker_symbol

    # ── Yahoo Finance 배당 이력 (3년) ──
    try:
        raw_div = ticker_obj.dividends
    except Exception as e:
        print(f"    배당 조회 실패: {e}")
        raw_div = pd.Series(dtype=float)

    def yahoo_row(dt: date, per_share: float) -> dict:
        total = per_share * shares
        after_tax = total * (1 - tax_rate)
        after_tax_krw = round(after_tax * usd_krw) if is_usd else round(after_tax)
        return {
            "date": dt.isoformat(),
            "per_share": round(per_share, 6),
            "after_tax_krw": after_tax_krw,
            "source": "yahoo",
        }

    history: list[dict] = []
    if not raw_div.empty:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=3)
        for ts, amount in raw_div[raw_div.index >= cutoff].items():
            history.append(yahoo_row(ts.date(), float(amount)))

    # ── 실수령 기록으로 병합/대체 ──
    # actual_records: 이 종목의 실수령 레코드 리스트
    actual_by_date: dict[str, dict] = {r["pay_date"]: r for r in actual_records}

    for act in actual_records:
        pay_date = act.get("pay_date")
        if not pay_date:
            continue
        amount_krw = act.get("amount_krw") or 0

        # Yahoo Finance 이력에서 같은 날짜(±15일 내) 항목 찾아서 대체
        matched = None
        act_dt = datetime.fromisoformat(pay_date).date()
        for row in history:
            row_dt = datetime.fromisoformat(row["date"]).date()
            if abs((act_dt - row_dt).days) <= 15:
                matched = row
                break

        if matched:
            matched["after_tax_krw"] = amount_krw
            matched["source"] = "actual"
            matched["actual_id"] = act.get("id")
        else:
            # Yahoo에 없는 실수령 기록 추가
            history.append({
                "date": pay_date,
                "per_share": act.get("per_share"),
                "after_tax_krw": amount_krw,
                "source": "actual",
                "actual_id": act.get("id"),
            })

    history.sort(key=lambda x: x["date"])

    if not history and not actual_records:
        return {
            "name": name, "market": market, "shares": shares,
            "frequency": "unknown", "no_dividend": True,
            "dividends_history": [], "dividends_projected": [],
        }

    # ── 빈도 감지 ──
    all_dates = [datetime.fromisoformat(d["date"]).date() for d in history]
    # 실수령 기록이 충분하면 실수령 기준으로 빈도 추정 (더 정확)
    actual_dates = [
        datetime.fromisoformat(r["pay_date"]).date()
        for r in actual_records if r.get("pay_date")
    ]
    freq_source = actual_dates if len(actual_dates) >= 2 else all_dates
    frequency = detect_frequency(freq_source)

    # ── 예측 ──
    # 예측 기준: 실수령 기록 우선, 없으면 Yahoo
    proj_base: list[dict] = []
    if actual_records:
        proj_base = [
            {"date": datetime.fromisoformat(r["pay_date"]).date(),
             "per_share": r.get("per_share"), "amount_krw": r.get("amount_krw")}
            for r in actual_records if r.get("pay_date")
        ]
    if not proj_base:
        proj_base = [
            {"date": datetime.fromisoformat(d["date"]).date(), "per_share": d["per_share"]}
            for d in history if d.get("per_share")
        ]

    raw_projected = project_dividends(proj_base, frequency)
    projected = []
    for p in raw_projected:
        if p.get("amount_krw"):
            after_tax_krw = p["amount_krw"]
        else:
            per_share = p.get("per_share") or 0
            total = per_share * shares
            after_tax = total * (1 - tax_rate)
            after_tax_krw = round(after_tax * usd_krw) if is_usd else round(after_tax)
        projected.append({
            "date": p["date"].isoformat(),
            "per_share": p.get("per_share"),
            "after_tax_krw": after_tax_krw,
            "projected": True,
            "source": "actual_based" if actual_records else "yahoo_based",
        })

    # ── 예측 정확도 (실수령 데이터가 있을 때) ──
    accuracy = None
    if actual_records:
        pairs = []
        for act in actual_records:
            act_dt = datetime.fromisoformat(act["pay_date"]).date()
            for row in history:
                if row.get("source") != "actual":
                    row_dt = datetime.fromisoformat(row["date"]).date()
                    if abs((act_dt - row_dt).days) <= 15:
                        yahoo_krw = row.get("after_tax_krw_yahoo") or row.get("after_tax_krw")
                        actual_krw = act.get("amount_krw", 0)
                        if yahoo_krw and actual_krw:
                            pairs.append(abs(yahoo_krw - actual_krw) / actual_krw * 100)
        if pairs:
            accuracy = round(100 - sum(pairs) / len(pairs), 1)

    return {
        "name": name,
        "market": market,
        "shares": shares,
        "frequency": frequency,
        "no_dividend": False,
        "actual_count": len(actual_records),
        "accuracy_pct": accuracy,
        "dividends_history": history,
        "dividends_projected": projected,
    }


# ── 월별 합계 ──────────────────────────────────────────────────────────────────
def build_monthly_summary(stocks: dict) -> dict:
    monthly: dict = {}
    for data in stocks.values():
        for div in data.get("dividends_history", []):
            m = div["date"][:7]
            bucket = monthly.setdefault(m, {"actual_krw": 0, "projected_krw": 0, "confirmed_krw": 0})
            if div.get("source") == "actual":
                bucket["confirmed_krw"] += div["after_tax_krw"]
            else:
                bucket["actual_krw"] += div["after_tax_krw"]

        for div in data.get("dividends_projected", []):
            m = div["date"][:7]
            bucket = monthly.setdefault(m, {"actual_krw": 0, "projected_krw": 0, "confirmed_krw": 0})
            bucket["projected_krw"] += div["after_tax_krw"]

    for m in monthly:
        monthly[m] = {k: round(v) for k, v in monthly[m].items()}
    return monthly


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    with open(HOLDINGS_FILE, "r", encoding="utf-8") as f:
        holdings_data = json.load(f)
    holdings = holdings_data.get("holdings", [])
    if not holdings:
        print("보유 종목이 없습니다."); sys.exit(1)

    print(f"총 {len(holdings)}개 종목 처리 시작\n")
    print("환율 조회 중...")
    usd_krw = get_usd_krw_rate()

    actual_map = load_actual_dividends()
    total_actual = sum(len(v) for v in actual_map.values())
    print(f"실수령 기록: {total_actual}건\n")

    stocks: dict = {}
    for h in holdings:
        ticker, market, shares = h["ticker"], h["market"], h["shares"]
        actual_recs = actual_map.get(ticker, [])
        print(f"[{ticker}] {h.get('name', '')} — {market} / {shares:,}주  (실수령 {len(actual_recs)}건)")
        stocks[ticker] = process_stock(ticker, market, shares, usd_krw, actual_recs)
        d = stocks[ticker]
        print(f"  → 빈도: {d['frequency']}, 이력: {len(d['dividends_history'])}건, 예측: {len(d['dividends_projected'])}건"
              + (f", 정확도: {d['accuracy_pct']}%" if d.get('accuracy_pct') is not None else ""))

    monthly_summary = build_monthly_summary(stocks)

    today = date.today()
    ytd_start = date(today.year, 1, 1).isoformat()

    # YTD: 실수령 확인분 + Yahoo 이력분
    ytd_total = sum(
        div["after_tax_krw"]
        for data in stocks.values()
        for div in data.get("dividends_history", [])
        if div["date"] >= ytd_start
    )
    # YTD 중 실수령 확인분만
    ytd_confirmed = sum(
        div["after_tax_krw"]
        for data in stocks.values()
        for div in data.get("dividends_history", [])
        if div["date"] >= ytd_start and div.get("source") == "actual"
    )

    year_str = str(today.year)
    expected_annual = ytd_total + sum(
        div["after_tax_krw"]
        for data in stocks.values()
        for div in data.get("dividends_projected", [])
        if div["date"].startswith(year_str)
    )

    this_month_key = today.strftime("%Y-%m")
    bkt = monthly_summary.get(this_month_key, {})
    this_month_total = bkt.get("confirmed_krw", 0) + bkt.get("actual_krw", 0) + bkt.get("projected_krw", 0)

    output = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "exchange_rate": {"USDKRW": round(usd_krw, 2)},
        "summary": {
            "ytd_total_krw": round(ytd_total),
            "ytd_confirmed_krw": round(ytd_confirmed),
            "expected_annual_krw": round(expected_annual),
            "this_month_krw": round(this_month_total),
            "total_tickers": len(holdings),
            "total_actual_records": total_actual,
        },
        "stocks": stocks,
        "monthly_summary": monthly_summary,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 완료 → {OUTPUT_FILE.name}")
    print(f"   YTD 수령액:        {ytd_total:>12,.0f}원")
    print(f"   YTD 실수령 확인:   {ytd_confirmed:>12,.0f}원")
    print(f"   연간 예상:         {expected_annual:>12,.0f}원")
    print(f"   이번 달 예상:      {this_month_total:>12,.0f}원")


if __name__ == "__main__":
    main()
