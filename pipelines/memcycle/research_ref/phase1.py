"""Phase 1: price-cycle calendar and stock-vs-price peak timing (Q3), per 预注册_周期定时与检验规则_20260928.md.

Reads the verified raw files under RAW (scratchpad) and writes CSVs + a JSON summary under OUT.
Run: python3 code/phase1.py
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from bb import date_turns, phases, _months_between  # noqa: E402

RAW = os.environ.get('MEMCYCLE_RAW', os.environ.get('MEMCYCLE_INPUTS', '/Users/songruoxuan/Desktop/Claude Code/存储周期研究/raw_local/raw'))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'phase1')

AMP_MAIN, AMP_SENS = 0.20, (0.30, 0.0)
SERIES_START = {'30911201AA': '1995-01', '30911202AA': '2000-01'}
PRODUCT = {'30911201AA': 'DRAM', '30911202AA': 'NAND'}

# group: pure (Q3 main) / diversified (reported separately); dram_maker: for the DRAM-only sensitivity
COMPANIES = [
    # key, label, file (relative to RAW), market, currency, group, dram_maker, nand_panel
    ('MU', 'Micron', 'us_eu_jp/MU_monthly.csv', 'US', 'USD', 'pure', True, True),
    ('000660', 'SK hynix / Hynix / Hyundai Elec.', 'kr/000660_monthly_official.csv', 'KR', 'KRW', 'pure', True, True),
    ('2408', 'Nanya Technology', 'tw/2408_monthly.csv', 'TW', 'TWD', 'pure', True, False),
    ('2344', 'Winbond', 'tw/2344_monthly.csv', 'TW', 'TWD', 'pure', True, False),
    ('5346', 'Powerchip (PSC)', 'tw/5346_monthly.csv', 'TW', 'TWD', 'pure', True, False),
    ('6770', 'Powerchip (PSMC)', 'tw/6770_monthly.csv', 'TW', 'TWD', 'pure', True, False),
    ('5387', 'ProMOS (not on the registered list; shown only)', 'tw/5387_monthly.csv', 'TW', 'TWD', 'unregistered', False, False),
    ('3474', 'Inotera', 'tw/3474_monthly.csv', 'TW', 'TWD', 'pure', True, False),
    ('ELPIDA', 'Elpida', 'delisted/elpida_full_monthly.csv', 'JP', 'JPY', 'pure', True, False),
    ('QI', 'Qimonda', 'delisted/qimonda_merged_monthly.csv', 'DE', 'USD', 'pure', True, False),
    ('SNDK_OLD', 'SanDisk (old, to 2016)', 'delisted/sandisk_old_monthly.csv', 'US', 'USD', 'pure', False, True),
    ('SNDK', 'SanDisk (new, 2025-)', 'us_eu_jp/SNDK_monthly.csv', 'US', 'USD', 'pure', False, True),
    ('285A', 'Kioxia', 'us_eu_jp/285A_T_monthly.csv', 'JP', 'JPY', 'pure', False, True),
    ('005930', 'Samsung Electronics', 'kr/005930_monthly_official.csv', 'KR', 'KRW', 'diversified', False, True),
    ('IFX', 'Infineon (to 2006 spin-off)', 'us_eu_jp/IFX_DE_monthly.csv', 'DE', 'EUR', 'diversified', False, False),
    ('WDC', 'Western Digital', 'us_eu_jp/WDC_monthly_spinoff_adjusted.csv', 'US', 'USD', 'diversified', False, False),
    # registered (diversified + NAND panel) but no clean history was fetched: Yahoo dropped 6502.T after the
    # Dec-2023 take-private; listed here so it shows up in summary.json's missing list instead of vanishing
    ('6502', 'Toshiba', 'us_eu_jp/6502_T_monthly.csv', 'JP', 'JPY', 'diversified', False, True),
    ('STX', 'Seagate', 'us_eu_jp/STX_monthly.csv', 'US', 'USD', 'diversified', False, False),
]
BENCH = {  # market -> list of (label, file); first entry is the primary benchmark
    'US': [('SOX', 'us_eu_jp/IDX_SOX_monthly.csv'), ('Nasdaq', 'us_eu_jp/IDX_IXIC_monthly.csv')],
    'KR': [('KOSPI', 'kr/KOSPI_monthly.csv')],
    'TW': [('TAIEX', 'tw/TAIEX_monthly.csv')],
    'JP': [('Nikkei 225', 'us_eu_jp/IDX_N225_monthly.csv')],
    'DE': [('SOX', 'us_eu_jp/IDX_SOX_monthly.csv'), ('DAX', 'us_eu_jp/IDX_GDAXI_monthly.csv')],
}
# Infineon counts as a memory company only until the Qimonda carve-out (May 2006), per §2 of the pre-registration
LAST_USE = {'IFX': '2006-04'}
DATA_END = '2026-08'  # last ECOS month; a company whose last close is earlier has delisted (or left scope)

FX = {'KRW': ('fred_DEXKOUS.csv', False), 'TWD': ('fred_DEXTAUS.csv', False),
      'JPY': ('fred_DEXJPUS.csv', False), 'EUR': ('fred_DEXUSEU.csv', True)}  # True = quoted USD per unit


def add_months(m: str, k: int) -> str:
    y, mo = map(int, m.split('-'))
    t = y * 12 + (mo - 1) + k
    return f'{t // 12}-{t % 12 + 1:02d}'


def month_range(a: str, b: str) -> list[str]:
    """Months in the half-open window (a, b]."""
    out, m = [], add_months(a, 1)
    while m <= b:
        out.append(m)
        m = add_months(m, 1)
    return out


def load_ecos():
    s = defaultdict(dict)
    with open(os.path.join(RAW, 'ecos', 'ecos_402Y016.csv')) as f:
        for r in csv.DictReader(f):
            s[(r['item_code'], r['basis'])][r['month']] = float(r['value'])
    return s


def load_monthly(rel: str):
    p = os.path.join(RAW, rel)
    if not os.path.exists(p):
        return None
    rows = {}
    with open(p) as f:
        for r in csv.DictReader(f):
            if not r.get('close') and not r.get('high'):
                continue
            rows[r['month']] = {  # close may be None (Elpida/Qimonda months with only a monthly high)
                'close': float(r['close']) if r.get('close') else None,
                'high': float(r['high']) if r.get('high') else None,
                'low': float(r['low']) if r.get('low') else None,
                'partial': 'partial' in (r.get('notes') or '').lower(),
            }
    return rows or None


def load_fx():
    out = {}
    for cur, (fn, usd_per_unit) in FX.items():
        last = {}
        with open(os.path.join(RAW, fn)) as f:
            for r in csv.DictReader(f):
                v = r.get(fn[5:-4]) or list(r.values())[1]
                if v in ('', '.'):
                    continue
                last[r[list(r)[0]][:7]] = float(v)  # rows are chronological -> last obs in month wins
        out[cur] = (last, usd_per_unit)
    return out


def to_usd(px: float, cur: str, month: str, fx) -> float | None:
    if cur == 'USD':
        return px
    table, usd_per_unit = fx[cur]
    r = table.get(month)
    if r is None:
        return None
    return px * r if usd_per_unit else px / r


def price_calendar(ecos):
    cal, sens = {}, []
    for item, start in SERIES_START.items():
        for basis in ('C', 'D', 'W'):
            ser = ecos[(item, basis)]
            months = sorted(m for m in ser if m >= start)
            vals = [ser[m] for m in months]
            for amp in (AMP_MAIN, *AMP_SENS):
                if basis != 'C' and amp != AMP_MAIN:
                    continue
                tp = date_turns(months, vals, amp)
                sens.append({'product': PRODUCT[item], 'basis': basis, 'amp': amp,
                             'turns': [(t.kind, t.month, t.confirmed) for t in tp]})
                if basis == 'C' and amp == AMP_MAIN:
                    cal[PRODUCT[item]] = {'turns': tp, 'phases': phases(tp), 'last_month': months[-1]}
    return cal, sens


def cycles_from(turns):
    """(T(k-1), P(k), T(k), P(k+1)) for every peak; missing ends are None."""
    out = []
    for i, t in enumerate(turns):
        if t.kind != 'P':
            continue
        prev_t = turns[i - 1] if i >= 1 and turns[i - 1].kind == 'T' else None
        next_t = turns[i + 1] if i + 1 < len(turns) else None
        next_p = turns[i + 2] if i + 2 < len(turns) else None
        prev_p = turns[i - 2] if i >= 2 else None
        out.append({'prev_trough': prev_t, 'peak': t, 'trough': next_t, 'next_peak': next_p, 'prev_peak': prev_p})
    return out


def analyse_company(px, cyc, latest, first, delisted):
    """Stock peak/trough and multiples for one company in one price cycle."""
    pk = cyc['peak']
    res = {'price_peak': pk.month, 'price_peak_confirmed': pk.confirmed}
    if cyc['prev_trough'] is None:
        res['status'] = 'excluded: no preceding price trough (first ECOS peak)'
        return res
    win_end = cyc['trough'].month if cyc['trough'] else latest
    win = month_range(cyc['prev_trough'].month, win_end)
    have = [m for m in win if m in px and px[m]['close'] is not None]
    res['window'] = f"({cyc['prev_trough'].month}, {win_end}]"
    # note 5: every month has a close, and neither the listing month nor the delisting month falls in the window
    res['complete'] = (len(have) == len(win) and first <= cyc['prev_trough'].month
                       and not (delisted and latest <= win_end))
    if not have:
        res['status'] = 'no data in window'
        return res
    sp = max(have, key=lambda m: (px[m]['close'], -int(m.replace('-', ''))))
    res['stock_peak'] = sp
    res['stock_peak_close'] = px[sp]['close']
    res['lead_months'] = _months_between(sp, pk.month)
    highs = [m for m in win if m in px and px[m]['high'] is not None]
    if highs:
        hp = max(highs, key=lambda m: (px[m]['high'], -int(m.replace('-', ''))))
        res['stock_peak_high_month'] = hp
        res['lead_months_high'] = _months_between(hp, pk.month)
    # trough after the price peak: (P(k), P(k+1)] or to latest
    t_end = cyc['next_peak'].month if cyc['next_peak'] else latest
    tw = [m for m in month_range(pk.month, t_end) if m in px and px[m]['close'] is not None]
    if tw:
        st = min(tw, key=lambda m: (px[m]['close'], int(m.replace('-', ''))))
        res['stock_trough'] = st
        res['stock_trough_close'] = px[st]['close']
        res['drawdown_to_trough'] = px[st]['close'] / px[sp]['close'] - 1 if st > sp else None
        if cyc['trough']:
            res['trough_lead_months'] = _months_between(st, cyc['trough'].month)
    # up-leg multiple: min close in (previous price peak, stock peak]
    up_start = cyc['prev_peak'].month if cyc['prev_peak'] else min(m for m in px if px[m]['close'] is not None)
    uw = [m for m in month_range(up_start, sp) if m in px and px[m]['close'] is not None]
    if uw:
        lo = min(uw, key=lambda m: (px[m]['close'], int(m.replace('-', ''))))
        res['upleg_low'] = lo
        res['upleg_low_close'] = px[lo]['close']
        res['upleg_multiple'] = px[sp]['close'] / px[lo]['close']
        res['upleg_start'] = up_start
        # display flag: the low sits in the first 3 months of trading after a listing that came after the previous
        # price peak, so the multiple is measured from (near) the IPO rather than from a cycle low
        res['upleg_truncated_by_listing'] = first > up_start and lo <= add_months(first, 2)
    # auxiliary basis (§3): intramonth low -> intramonth high, same windows
    if highs:
        lw = [m for m in month_range(up_start, hp) if m in px and px[m]['low'] is not None]
        if lw:
            lo_hl = min(lw, key=lambda m: (px[m]['low'], int(m.replace('-', ''))))
            res['upleg_multiple_hl'] = px[hp]['high'] / px[lo_hl]['low']
            res['upleg_low_hl_month'] = lo_hl
    res['status'] = 'ok'
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    ecos = load_ecos()
    cal, sens = price_calendar(ecos)
    fx = load_fx()

    with open(os.path.join(OUT, 'price_turns.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['product', 'basis', 'amp_threshold', 'kind', 'month', 'confirmed'])
        for s in sens:
            for k, m, c in s['turns']:
                w.writerow([s['product'], s['basis'], s['amp'], k, m, c])
    with open(os.path.join(OUT, 'price_phases_main.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['product', 'from', 'from_kind', 'to', 'to_kind', 'months', 'change', 'to_confirmed'])
        for prod, c in cal.items():
            for p in c['phases']:
                w.writerow([prod, p['from'], p['from_kind'], p['to'], p['to_kind'], p['months'],
                            round(p['change'], 4), p['to_confirmed']])

    bench = {mk: [(lab, load_monthly(fn)) for lab, fn in lst] for mk, lst in BENCH.items()}
    rows, missing = [], []
    for key, label, fn, market, cur, group, dram_maker, nand in COMPANIES:
        px = load_monthly(fn)
        if px is None:
            missing.append(f'{key} ({fn})')
            continue
        if key in LAST_USE:
            px = {m: v for m, v in px.items() if m <= LAST_USE[key]}
        latest = max(m for m in px if px[m]['close'] is not None)
        first = min(m for m in px if px[m]['close'] is not None)
        delisted = latest < DATA_END
        for prod, c in cal.items():
            if prod == 'NAND' and not nand:
                continue
            for cyc in cycles_from(c['turns']):
                r = analyse_company(px, cyc, latest, first, delisted)
                r.update({'product': prod, 'company': key, 'label': label, 'group': group,
                          'dram_maker': dram_maker, 'market': market, 'currency': cur,
                          'first_month': first, 'last_month': latest, 'delisted_or_out_of_scope': delisted})
                if r.get('status') == 'ok':
                    lo, sp = r['upleg_low'], r['stock_peak']
                    ulo, usp = to_usd(px[lo]['close'], cur, lo, fx), to_usd(px[sp]['close'], cur, sp, fx)
                    r['upleg_multiple_usd'] = usp / ulo if ulo and usp else None
                    # alternative: pick the low and the peak on the USD-converted series itself (same windows)
                    usd = {m: to_usd(v['close'], cur, m, fx) for m, v in px.items() if v['close'] is not None}
                    pwin = [m for m in month_range(cyc['prev_trough'].month, cyc['trough'].month if cyc['trough'] else latest)
                            if usd.get(m)]
                    if pwin:
                        usp2 = max(pwin, key=lambda m: (usd[m], -int(m.replace('-', ''))))
                        uw2 = [m for m in month_range(r['upleg_start'], usp2) if usd.get(m)]
                        if uw2:
                            r['upleg_multiple_usd_series'] = usd[usp2] / min(usd[m] for m in uw2)
                    for i, (blab, bpx) in enumerate(bench[market]):
                        if bpx and lo in bpx and sp in bpx:
                            bm = bpx[sp]['close'] / bpx[lo]['close']
                            r[f'bench{i + 1}'] = blab
                            r[f'bench{i + 1}_multiple'] = bm
                            r[f'excess{i + 1}'] = r['upleg_multiple'] / bm
                    r['partial_latest_month'] = any(px[m]['partial'] for m in (sp, r.get('stock_trough', sp)))
                rows.append(r)

    fields = ['product', 'company', 'label', 'group', 'dram_maker', 'market', 'currency', 'first_month', 'last_month',
              'price_peak', 'price_peak_confirmed', 'window', 'complete', 'status', 'stock_peak', 'stock_peak_close',
              'lead_months', 'stock_peak_high_month', 'lead_months_high', 'stock_trough', 'stock_trough_close',
              'trough_lead_months', 'drawdown_to_trough', 'upleg_low', 'upleg_low_close', 'upleg_multiple',
              'upleg_start', 'upleg_truncated_by_listing', 'upleg_multiple_hl', 'upleg_low_hl_month', 'upleg_multiple_usd',
              'upleg_multiple_usd_series', 'delisted_or_out_of_scope', 'bench1', 'bench1_multiple', 'excess1', 'bench2',
              'bench2_multiple', 'excess2', 'partial_latest_month']
    with open(os.path.join(OUT, 'company_cycles.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})

    def verdict(sample):
        leads = [r['lead_months'] for r in sample]
        if not leads:
            return {'n': 0}
        share = sum(1 for x in leads if x >= 1) / len(leads)
        med = statistics.median(leads)
        v = ('holds' if share >= 0.6 and 3 <= med <= 9 else 'falsified' if share < 0.5 else 'inconclusive')
        return {'n': len(leads), 'share_lead_ge1': round(share, 3), 'median_lead': med,
                'share_lead_3_to_9': round(sum(1 for x in leads if 3 <= x <= 9) / len(leads), 3),
                'leads': leads, 'verdict': v}

    def eligible(r, prod):
        # pre-reg notes 4-5: confirmed price peak with a preceding trough, complete monthly closes in the window
        return r['product'] == prod and r.get('status') == 'ok' and r['complete'] and r['price_peak_confirmed']

    q3 = {
        'main_pure': verdict([r for r in rows if eligible(r, 'DRAM') and r['group'] == 'pure']),
        'sens_dram_makers_only': verdict([r for r in rows if eligible(r, 'DRAM') and r['dram_maker']]),
        'diversified_dram': verdict([r for r in rows if eligible(r, 'DRAM') and r['group'] == 'diversified']),
        'nand_panel': verdict([r for r in rows if eligible(r, 'NAND')]),
    }
    summary = {
        'calendar': {p: {'turns': [(t.kind, t.month, t.confirmed) for t in c['turns']], 'last_month': c['last_month']}
                     for p, c in cal.items()},
        'sensitivity': sens,
        'q3': q3,
        'missing_company_files': missing,
    }
    with open(os.path.join(OUT, 'summary.json'), 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps({'calendar': summary['calendar'], 'q3': {k: {kk: vv for kk, vv in v.items() if kk != 'leads'}
                                                              for k, v in q3.items()},
                      'missing': missing}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
