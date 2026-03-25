#!/usr/bin/env python3
"""추가수당 계산기 + 실수령액 (2026 기준)

사용법:
  대화형:  python3 pay_calc.py
  자동:    python3 pay_calc.py --overtime=816 --night=2037 --holiday=833 --paid-recog=1440 --work-days=22 --month="2026년 2월"
"""

import argparse
import sys

# ── 2026년 4대보험 요율 ─────────────────────────────────────────────
PENSION_RATE    = 0.0475   # 국민연금 4.75% (2026, +0.25%p)
HEALTH_RATE     = 0.03595  # 건강보험 3.595% (2026, +0.05%p)
LONG_CARE_RATE  = 0.1314   # 장기요양 건강보험료 × 13.14% (2026)
EMPLOYMENT_RATE = 0.009    # 고용보험 0.9% (동결)

# ── 개인 고정값 ──────────────────────────────────────────────────────
DEFAULT = dict(
    통상임금       = 9_404_285,   # 9,130,374 × 1.03
    기준시간       = 209,
    기본급        = 9_155_310,   # 8,888,650 × 1.03
    국민연금기준소득 = 6_370_000,  # 기준소득월액 → × 4.75% = 302,575
    건강보험기준보수 = 10_571_650, # 보수월액 → × 3.595% = 380,051
    대출금상환     = 833_333,
    기타공제       = 0,
    부양가족       = 1,
    건강연말정산    = 95_040,     # 건강보험 연말정산 (매년 1월 자동 공제, 전년 정산)
)

# ── 명절상여 스케줄 (당일 재직 시 200,000원) ────────────────────────
# 급여월(YYYY-MM): 지급=공제로 실수령 무영향, 소득세 과세소득에만 포함됨
HOLIDAY_BONUS = {
    "2026-02": 200_000,  # 설날 2026-02-17
    "2026-10": 200_000,  # 추석 2026-10-04
}


# ── 소득세 계산 ──────────────────────────────────────────────────────

def earned_income_deduction(annual):
    """근로소득공제 (한도 2,000만원)"""
    if annual <= 5_000_000:
        d = annual * 0.70
    elif annual <= 15_000_000:
        d = 3_500_000 + (annual - 5_000_000) * 0.40
    elif annual <= 45_000_000:
        d = 7_500_000 + (annual - 15_000_000) * 0.15
    elif annual <= 100_000_000:
        d = 12_000_000 + (annual - 45_000_000) * 0.05
    else:
        d = 14_500_000 + (annual - 100_000_000) * 0.02
    return min(d, 20_000_000)


def calc_income_tax(taxable):
    """누진 세율 적용 (2025/2026 동일)"""
    brackets = [
        (14_000_000,    0.06),
        (50_000_000,    0.15),
        (88_000_000,    0.24),
        (150_000_000,   0.35),
        (300_000_000,   0.38),
        (500_000_000,   0.40),
        (1_000_000_000, 0.42),
        (float('inf'),  0.45),
    ]
    tax, prev = 0, 0
    for limit, rate in brackets:
        if taxable <= prev:
            break
        tax += (min(taxable, limit) - prev) * rate
        prev = limit
    return tax


def estimate_monthly_tax(gross, dependents, pension, health, long_care, employment):
    """간이세액표 근사 (당월 × 12 연산)"""
    annual  = gross * 12
    ei      = earned_income_deduction(annual)
    taxable = max(0, annual - ei
                     - dependents * 1_500_000
                     - (pension + health + long_care + employment) * 12)
    raw     = calc_income_tax(taxable)
    credit  = min(
        715_000 + (raw - 1_300_000) * 0.30 if raw > 1_300_000 else raw * 0.55,
        500_000 if annual > 70_000_000 else
        660_000 if annual > 33_000_000 else 740_000,
    )
    return max(0, raw - credit) / 12


# ── 핵심 계산 로직 ───────────────────────────────────────────────────

def run(overtime_min, night_min, holiday_min, hol_ext_min, paid_recog_min,
        work_days, fitness_ok,
        통상임금, 기준시간, 기본급,
        pension_base, health_base, loan, other, dependents,
        pay_month="당월"):

    rate      = 통상임금 / 기준시간
    pension   = round(pension_base * PENSION_RATE)
    health    = round(health_base  * HEALTH_RATE)
    long_care = round(health * LONG_CARE_RATE)

    # ── 지급 ─────────────────────────────────────────────────────────
    overtime_pay   = overtime_min   / 60 * rate * 1.5  # 전월연장: 통상시급 × 1.5
    night_pay      = night_min      / 60 * rate * 0.5  # 전월야간: 통상시급 × 0.5
    holiday_pay    = holiday_min    / 60 * rate * 0.5  # 전월휴일: 통상시급 × 0.5
    hol_ext_pay    = hol_ext_min    / 60 * rate * 0.5  # 휴일연장: 통상시급 × 0.5
    paid_recog_pay = paid_recog_min / 60 * rate * 1.0  # 전월유급인정: 통상시급 × 1.0
    comm_pay       = 100_000 if work_days >= 15 else 0
    fitness_pay    = 100_000 if (work_days >= 15 and fitness_ok) else 0

    # 명절상여: 해당 급여월 자동 반영 (지급=공제 상쇄, 실수령 무영향)
    holiday_bonus = 0
    for ym, amt in HOLIDAY_BONUS.items():
        if ym in pay_month:
            holiday_bonus = amt
            break

    total_gross = (기본급
                   + overtime_pay + night_pay + holiday_pay
                   + hol_ext_pay + paid_recog_pay
                   + comm_pay + fitness_pay
                   + holiday_bonus)

    # ── 공제 ─────────────────────────────────────────────────────────
    employment = round(total_gross * EMPLOYMENT_RATE)
    income_tax = estimate_monthly_tax(total_gross, dependents,
                                      pension, health, long_care, employment)
    local_tax  = income_tax * 0.10

    # 건강보험 연말정산: 급여월에 "1월" 포함 시 자동 반영
    health_adj = DEFAULT['건강연말정산'] if '1월' in pay_month else 0

    total_ded  = (pension + health + long_care + employment
                  + income_tax + local_tax + loan + other
                  + holiday_bonus + health_adj)

    net = total_gross - total_ded

    # ── 출력 ─────────────────────────────────────────────────────────
    W = 60

    def fmt(n):
        return f"{n:,.0f}원"

    def row(label, amount, note=""):
        print(f"  {label:<16} {fmt(amount):>13}  {note}")

    def mins_str(m):
        h, mm = divmod(int(m), 60)
        return f"{h}h {mm}m" if mm else f"{h}h"

    print()
    print("=" * W)
    print(f"   {pay_month} 급여 예상  (통상시급 {rate:,.0f}원)")
    print("=" * W)
    print("  ▶ 지급")
    row("기본급", 기본급)

    for label, mins, pay, mult in [
        ("전월연장근무수당",  overtime_min,   overtime_pay,   "× 1.5"),
        ("전월야간근무수당",  night_min,      night_pay,      "× 0.5"),
        ("전월휴일근무수당",  holiday_min,    holiday_pay,    "× 0.5"),
        ("휴일연장근무수당",  hol_ext_min,    hol_ext_pay,    "× 0.5"),
        ("전월유급인정수당",  paid_recog_min, paid_recog_pay, "× 1.0"),
    ]:
        if mins > 0:
            row(label, pay, f"[{mins_str(mins)} {mult}]")
        else:
            print(f"  {label:<16} {'해당없음':>13}")

    row("통신비",    comm_pay,
        "" if work_days >= 15 else "(15일 미만 미지급)")
    row("체력단련비", fitness_pay,
        "" if (work_days >= 15 and fitness_ok)
           else "(15일 미만)" if work_days < 15 else "(미신청)")
    if holiday_bonus:
        row("명절상여", holiday_bonus, "(지급=공제 상쇄)")

    print("-" * W)
    row("총 지급액", total_gross)

    print("-" * W)
    print("  ▶ 공제")
    row("국민연금",    pension,
        f"(기준소득 {pension_base:,}원 × {PENSION_RATE*100:.2f}%)")
    row("건강보험",    health,
        f"(보수월액 {health_base:,}원 × {HEALTH_RATE*100:.3f}%)")
    row("장기요양보험", long_care,
        f"(건강보험 × {LONG_CARE_RATE*100:.2f}%)")
    row("고용보험",    employment,
        f"(총지급 × {EMPLOYMENT_RATE*100:.1f}%)")
    row("소득세",     income_tax, "(간이세액표 근사)")
    row("지방소득세",  local_tax,  "(소득세 × 10%)")
    if loan:
        row("대출금상환", loan)
    if other:
        row("기타공제",   other)
    if holiday_bonus:
        row("명절상여(소득세반영)", holiday_bonus, "(지급 상쇄)")
    if health_adj:
        row("건강보험연말정산", health_adj, "(전년 정산, 1월 자동)")

    print("-" * W)
    row("총 공제액", total_ded)

    print("=" * W)
    print(f"  {'★ 실수령액':<16} {fmt(net):>13}")
    print("=" * W)
    print()
    print("※ 소득세는 간이세액표 근사값 (연말정산으로 확정)")
    print(f"※ 국민연금 {PENSION_RATE*100:.2f}% / "
          f"건강보험 {HEALTH_RATE*100:.3f}% / "
          f"장기요양 {LONG_CARE_RATE*100:.2f}% (2026)")


# ── 대화형 모드 ──────────────────────────────────────────────────────

def get_num(prompt, default=0, is_int=False):
    val = input(prompt).strip()
    if not val:
        return default
    cleaned = val.replace(",", "")
    return int(cleaned) if is_int else float(cleaned)


def interactive():
    D = DEFAULT
    W = 60
    print("=" * W)
    print("         추가수당 계산기 + 실수령액 (2026)")
    print("=" * W)

    print("\n[ 통상시급 산출 ]")
    통상임금 = get_num(f"  통상임금 총액 [{D['통상임금']:,}원]: ", D['통상임금'])
    기준시간  = get_num(f"  기준시간      [{D['기준시간']}시간]: ",  D['기준시간'])
    print(f"  → 통상시급 : {통상임금:,}원 ÷ {기준시간}h = {통상임금/기준시간:,.1f}원")
    기본급 = get_num(f"\n기본급 [{D['기본급']:,}원]: ", D['기본급'])

    print("\n[ 시간 외 근무 ] (없으면 Enter)")
    overtime_min   = get_num("  전월 연장근무시간    (분): ")
    night_min      = get_num("  전월 야간근무시간    (분): ")
    holiday_min    = get_num("  전월 휴일근무시간    (분): ")
    hol_ext_min    = get_num("  전월 휴일연장근무시간 (분): ")
    paid_recog_min = get_num("  전월 유급인정시간    (분): ")
    work_days      = get_num("  실근무일수          (일): ", is_int=True)
    fitness_ok     = input(  "  체력단련비 신청? (y/n) [y]: ").strip().lower() != "n"

    print("\n[ 고정 공제 ] (변경 없으면 Enter)")
    pension_base = get_num(
        f"  국민연금 기준소득월액 [{D['국민연금기준소득']:,}원]: ", D['국민연금기준소득'])
    health_base  = get_num(
        f"  건강보험 보수월액     [{D['건강보험기준보수']:,}원]: ", D['건강보험기준보수'])
    print(f"  → 국민연금   : {round(pension_base * PENSION_RATE):,}원")
    print(f"  → 건강보험   : {round(health_base * HEALTH_RATE):,}원")
    print(f"  → 장기요양   : {round(round(health_base * HEALTH_RATE) * LONG_CARE_RATE):,}원")
    loan  = get_num(f"  대출금상환 [{D['대출금상환']:,}원]: ", D['대출금상환'])
    other = get_num("  기타공제   [0원]: ", 0)

    print("\n[ 소득세 ]")
    dependents = get_num(
        f"  공제대상 가족수 (본인 포함) [{D['부양가족']}명]: ",
        D['부양가족'], is_int=True)

    pay_month = input("\n급여월 (예: 2026년 3월) [당월]: ").strip() or "당월"

    run(overtime_min, night_min, holiday_min, hol_ext_min, paid_recog_min,
        work_days, fitness_ok,
        통상임금, 기준시간, 기본급,
        pension_base, health_base, loan, other, dependents,
        pay_month=pay_month)


# ── CLI 자동 모드 ────────────────────────────────────────────────────

def auto():
    D = DEFAULT
    p = argparse.ArgumentParser(description="추가수당 + 실수령액 계산기 (2026)")
    p.add_argument("--overtime",      type=float, default=0,
                   help="전월 연장근무시간(분)")
    p.add_argument("--night",         type=float, default=0,
                   help="전월 야간근무시간(분)")
    p.add_argument("--holiday",       type=float, default=0,
                   help="전월 휴일근무시간(분)")
    p.add_argument("--hol-ext",       type=float, default=0,
                   help="전월 휴일연장근무시간(분)")
    p.add_argument("--paid-recog",    type=float, default=0,
                   help="전월 유급인정시간(분)")
    p.add_argument("--work-days",     type=int,   default=22,
                   help="실근무일수")
    p.add_argument("--no-fitness",    action="store_true",
                   help="체력단련비 미신청")
    p.add_argument("--allowance",     type=float, default=D['통상임금'],
                   help="통상임금 총액")
    p.add_argument("--std-hours",     type=float, default=D['기준시간'],
                   help="기준시간")
    p.add_argument("--base-pay",      type=float, default=D['기본급'],
                   help="기본급")
    p.add_argument("--pension-base",  type=float, default=D['국민연금기준소득'],
                   help="국민연금 기준소득월액")
    p.add_argument("--health-base",   type=float, default=D['건강보험기준보수'],
                   help="건강보험 보수월액")
    p.add_argument("--loan",          type=float, default=D['대출금상환'],
                   help="대출금상환(월)")
    p.add_argument("--other",         type=float, default=D['기타공제'],
                   help="기타공제(월)")
    p.add_argument("--dependents",    type=int,   default=D['부양가족'],
                   help="공제대상 가족수")
    p.add_argument("--month",         type=str,   default="당월",
                   help="급여월 (예: 2026년 2월) — 1월이면 건강연말정산 자동 반영")
    args = p.parse_args()

    run(args.overtime, args.night, args.holiday, args.hol_ext, args.paid_recog,
        args.work_days, not args.no_fitness,
        args.allowance, args.std_hours, args.base_pay,
        args.pension_base, args.health_base, args.loan, args.other, args.dependents,
        pay_month=args.month)


# ── 진입점 ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        auto()
    else:
        interactive()
