"""One-off export (run locally on 2026-09-28) of everything the memcycle port needs that cannot be rebuilt in CI.

The stock and benchmark closes behind the lead/multiple results come from vendors whose terms forbid
redistribution (Yahoo, KRX via Naver/Daum, Nikkei, FinMind/TWSE mirrors, archived pages), and this repo is
public. So they stay in the owner's local research folder (MEMCYCLE_INPUTS, default below), and only these
go into the repo:
  - data/raw/memcycle/ecos/2026-09-28/1316/*.gz   ECOS 402Y016 raw API responses (BOK terms allow reuse with
                                                  attribution; see docs/memcycle/PLAN.md)
  - data/case_studies/memcycle/frozen/            derived stock-side results: months, lead months, ratios, flags.
                                                  The three close-level columns are dropped.
  - data/case_studies/memcycle/inputs_manifest.json   sha256 + coverage of every local input
  - data/case_studies/memcycle/q3_versions.json   the Q3 verdict at each research commit (disclosure)
  - pipelines/memcycle/tests/fixtures/golden/     research outputs at the research repo's HEAD, for the port's
                                                  reproduction tests
The proper pipelines/memcycle/freeze.py replaces this script; a local test must show it reproduces these files.
Run from the repo root:  python pipelines/memcycle/research_ref/freeze_v0.py
"""
import csv
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys

RESEARCH = os.environ.get('MEMCYCLE_RESEARCH', '/Users/songruoxuan/Desktop/Claude Code/存储周期研究')
INPUTS = os.environ.get('MEMCYCLE_INPUTS', os.path.join(RESEARCH, 'raw_local', 'raw'))
DROP = {'stock_peak_close', 'stock_trough_close', 'upleg_low_close'}
Q3_COMMITS = ['5c64b64', '26a7377', '134c642', 'f308ad8']


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def git(*a):
    env = {**os.environ, 'DEVELOPER_DIR': '/Library/Developer/CommandLineTools'}
    return subprocess.run(['git', '-C', RESEARCH, *a], capture_output=True, text=True, check=True, env=env).stdout


def main():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    import phase1  # the research code, reading INPUTS

    # 1. ECOS raw responses -> data/raw (append-only layer)
    raw_dir = 'data/raw/memcycle/ecos/2026-09-28/1316'
    os.makedirs(raw_dir, exist_ok=True)
    for fn in ('ecos_raw_responses.jsonl', 'ecos_pull_log.json', 'ecos_itemlist_402Y016.json'):
        src = os.path.join(INPUTS, 'ecos', fn)
        with open(src, 'rb') as fi, gzip.GzipFile(os.path.join(raw_dir, fn + '.gz'), 'wb', mtime=0) as fo:
            shutil.copyfileobj(fi, fo)

    # 2. derived stock-side table without close levels
    frozen = 'data/case_studies/memcycle/frozen'
    os.makedirs(frozen, exist_ok=True)
    rows = list(csv.DictReader(open(os.path.join(RESEARCH, 'data/phase1/company_cycles.csv'))))
    keep = [c for c in rows[0] if c not in DROP]
    for path in (os.path.join(frozen, 'company_cycles_derived.csv'),):
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=keep, extrasaction='ignore')
            w.writeheader()
            w.writerows(rows)

    # 3. golden fixtures (research outputs at HEAD)
    gold = 'pipelines/memcycle/tests/fixtures/golden'
    os.makedirs(gold, exist_ok=True)
    for fn in ('price_turns.csv', 'price_phases_main.csv', 'summary.json'):
        shutil.copyfile(os.path.join(RESEARCH, 'data/phase1', fn), os.path.join(gold, fn))
    shutil.copyfile(os.path.join(frozen, 'company_cycles_derived.csv'), os.path.join(gold, 'company_cycles_derived.csv'))
    shutil.copyfile(os.path.join(INPUTS, 'ecos', 'ecos_402Y016.csv'), os.path.join(gold, 'ecos_402Y016.csv'))

    # 4. Q3 verdict at each research commit
    versions = []
    for c in Q3_COMMITS:
        s = json.loads(git('show', f'{c}:data/phase1/summary.json'))
        msg = git('log', '-1', '--format=%H%x09%aI%x09%s', c).strip().split('\t')
        versions.append({'research_commit': msg[0][:7], 'committed_at': msg[1], 'subject': msg[2],
                         'q3': {k: {kk: vv for kk, vv in v.items() if kk != 'leads'} for k, v in s['q3'].items()}})
    json.dump({'note': 'Q3 as computed at each research commit; rules registered at e72dacd (13:11 -07:00), '
                       'first computed at 5c64b64, roster corrected to the registered list at 134c642/f308ad8.',
               'versions': versions}, open('data/case_studies/memcycle/q3_versions.json', 'w'), indent=1)

    # 5. manifest of every local input phase1.py reads
    files = {'ecos/ecos_402Y016.csv'} | {f for _, _, f, *_ in phase1.COMPANIES} | \
            {f for lst in phase1.BENCH.values() for _, f in lst} | {fn for fn, _ in phase1.FX.values()}
    files |= {'delisted/elpida_quarterly_hl.csv', 'delisted/qimonda_quarterly_hl.csv',
              'delisted/elpida_monthend_close_partial.csv', 'delisted/qimonda_monthend_close_partial.csv',
              'delisted_verify/elpida_monthly_from_kabudragon.csv',
              'kr/000660_monthly.csv', 'kr/005930_monthly.csv', 'kr/005935_monthly.csv',
              'kr/000660_yahoo_monthly.csv', 'kr/005930_yahoo_monthly.csv', 'kr/005935_yahoo_monthly.csv'}
    man = []
    for f in sorted(files):
        p = os.path.join(INPUTS, f)
        if not os.path.exists(p):
            man.append({'path': f, 'present': False})
            continue
        entry = {'path': f, 'present': True, 'bytes': os.path.getsize(p), 'sha256': sha(p)}
        if f.endswith('.csv'):
            rs = list(csv.DictReader(open(p)))
            entry['rows'] = len(rs)
            key = 'month' if rs and 'month' in rs[0] else ('period' if rs and 'period' in rs[0] else None)
            if key:
                ms = sorted(r[key] for r in rs if r.get(key))
                entry['first'], entry['last'] = ms[0], ms[-1]
            for k in ('source', 'adj_basis', 'currency'):
                if rs and k in rs[0]:
                    entry[k] = rs[0][k][:200]
        man.append(entry)
    head = git('rev-parse', 'HEAD').strip()
    scripts = {fn: sha(os.path.join(RESEARCH, 'code', fn)) for fn in ('bb.py', 'phase1.py', 'prepare_inputs.py', 'phase1_tables.py')}
    json.dump({'note': 'Local inputs (not redistributed; not re-verifiable in CI). Paths are relative to MEMCYCLE_INPUTS.',
               'research_repo_head': head, 'research_scripts_sha256': scripts, 'inputs': man},
              open('data/case_studies/memcycle/inputs_manifest.json', 'w'), indent=1)
    print('manifest entries:', len(man), 'missing:', [m['path'] for m in man if not m['present']])


if __name__ == '__main__':
    main()
