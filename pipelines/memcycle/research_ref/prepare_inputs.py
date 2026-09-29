"""Merge the partial month-end closes and the monthly high/low rows for Elpida and Qimonda into one
monthly file each, so phase1.py can read them like any other company.  Months without a close keep
close blank, which makes every cycle window that contains them 'incomplete' (excluded from the Q3
verdict, per the pre-registration); the monthly high is kept for the high-based annotation only.
"""
import csv
import os

RAW = os.environ.get('MEMCYCLE_RAW', os.environ.get('MEMCYCLE_INPUTS', '/Users/songruoxuan/Desktop/Claude Code/存储周期研究/raw_local/raw'))
FIELDS = ['ticker', 'entity', 'month', 'close', 'high', 'low', 'month_end_date', 'currency', 'adj_basis', 'source', 'notes']

for name in ('elpida', 'qimonda'):
    closes = {r['month']: r for r in csv.DictReader(open(os.path.join(RAW, 'delisted', f'{name}_monthend_close_partial.csv')))}
    hl = {r['period']: r for r in csv.DictReader(open(os.path.join(RAW, 'delisted', f'{name}_quarterly_hl.csv')))
          if r['period_type'].startswith('month')}
    months = sorted(set(closes) | set(hl))
    ref = next(iter(closes.values()))
    with open(os.path.join(RAW, 'delisted', f'{name}_merged_monthly.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for m in months:
            c, h = closes.get(m, {}), hl.get(m, {})
            w.writerow({'ticker': ref['ticker'], 'entity': ref['entity'], 'month': m,
                        'close': c.get('close', ''), 'high': h.get('high', ''), 'low': h.get('low', ''),
                        'month_end_date': c.get('month_end_date', ''), 'currency': ref['currency'],
                        'adj_basis': ref['adj_basis'],
                        'source': ' | '.join(filter(None, [c.get('source'), h.get('source')])),
                        'notes': ' | '.join(filter(None, [c.get('notes'), h.get('period_type') and f"hl:{h['period_type']}"]))})
    print(name, len(months), 'months,', len(closes), 'with close')

# --- fixes from the data verifiers (notes/*_verify.md) ---
import shutil  # noqa: E402

# Elpida: the verifier rebuilt all 89 month-end closes from kabudragon (cross-checked against the 46 archived
# closes, 87 statutory monthly high/low values and 879 daily bars); use it instead of the partial merge above.
shutil.copyfile(os.path.join(RAW, 'delisted_verify', 'elpida_monthly_from_kabudragon.csv'),
                os.path.join(RAW, 'delisted', 'elpida_full_monthly.csv'))
print('elpida: full 89-month file from the verifier')

# Korea: since the KRX after-market launch on 2026-09-14, Daum/Naver 'closes' are after-market last trades.
# The official regular-session close equals Yahoo's (23/23 stock-days checked), so months >= 2026-09 take Yahoo's row.
for t in ('000660', '005930', '005935'):
    base = list(csv.DictReader(open(os.path.join(RAW, 'kr', f'{t}_monthly.csv'))))
    yahoo = {r['month']: r for r in csv.DictReader(open(os.path.join(RAW, 'kr', f'{t}_yahoo_monthly.csv')))}
    with open(os.path.join(RAW, 'kr', f'{t}_monthly_official.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(base[0].keys()))
        w.writeheader()
        for r in base:
            if r['month'] >= '2026-09' and r['month'] in yahoo:
                y = yahoo[r['month']]
                r = {**r, 'close': y['close'], 'high': y['high'], 'low': y['low'],
                     'source': 'Yahoo (official regular-session close after the 2026-09-14 after-market launch)',
                     'notes': (r.get('notes') or '') + ' | replaced by Yahoo regular-session close'}
            w.writerow(r)
    print(t, 'official-close file written')
