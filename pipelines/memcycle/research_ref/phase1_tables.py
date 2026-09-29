"""Render the Phase 1 tables (markdown) from data/phase1/*.  Numbers are never typed by hand.

Run after phase1.py:  python3 code/phase1_tables.py > data/phase1/tables.md
"""
from __future__ import annotations

import csv
import json
import os
import statistics
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(HERE, 'data', 'phase1')


def _mb(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def pct(x: float) -> str:
    return f'{x * 100:+.0f}%'


def load():
    s = json.load(open(os.path.join(D, 'summary.json')))
    rows = list(csv.DictReader(open(os.path.join(D, 'company_cycles.csv'))))
    ph = list(csv.DictReader(open(os.path.join(D, 'price_phases_main.csv'))))
    return s, rows, ph


def calendar_table(prod, ph):
    out = [f'| 轮 | 谷 | 峰 | 谷 | 上行 | 下行 |', '|---|---|---|---|---|---|']
    ps = [p for p in ph if p['product'] == prod]
    n = 0
    for i, p in enumerate(ps):
        if p['from_kind'] != 'T':
            continue
        n += 1
        up = p
        down = ps[i + 1] if i + 1 < len(ps) else None
        conf = '' if up['to_confirmed'] == 'True' else '（未确认）'
        up_s = f"{up['months']} 个月，{pct(float(up['change']))}"
        down_s = (f"{down['months']} 个月，{pct(float(down['change']))}" if down else '进行中')
        out.append(f"| {n} | {up['from']} | {up['to']}{conf} | {down['to'] if down else '—'} | {up_s} | {down_s} |")
    first = ps[0] if ps else None
    if first and first['from_kind'] == 'P':
        out.insert(2, f"| 0 | —（序列开始前） | {first['from']} | {first['to']} | 不按规则定时 | "
                      f"{first['months']} 个月，{pct(float(first['change']))} |")
    return '\n'.join(out)


def sensitivity_table(s, prod):
    main = next(x for x in s['sensitivity'] if x['product'] == prod and x['basis'] == 'C' and x['amp'] == 0.2)
    base = {(k, m) for k, m, _ in main['turns']}
    out = ['| 口径 | 门槛 | 拐点数 | 与主规则不同的拐点 |', '|---|---|---|---|']
    for x in s['sensitivity']:
        if x['product'] != prod:
            continue
        cur = sorted({(k, m) for k, m, _ in x['turns']}, key=lambda t: t[1])
        # pair each main-rule turn with the nearest same-kind turn within 6 months; the rest are added/dropped
        free, shifts, drop = list(cur), [], []
        for k, m in sorted(base, key=lambda t: t[1]):
            cand = [c for c in free if c[0] == k and abs(_mb(m, c[1])) <= 6]
            if not cand:
                drop.append((k, m))
                continue
            c = min(cand, key=lambda c: abs(_mb(m, c[1])))
            free.remove(c)
            if c[1] != m:
                shifts.append(f'{k}{m}→{c[1]}（{_mb(m, c[1]):+d}）')
        diff = '；'.join(filter(None, [
            ('平移 ' + '、'.join(shifts)) if shifts else '',
            ('多出 ' + '、'.join(f'{k}{m}' for k, m in free)) if free else '',
            ('少了 ' + '、'.join(f'{k}{m}' for k, m in drop)) if drop else '']))
        basis = {'C': '合同货币', 'D': '美元', 'W': '韩元'}[x['basis']]
        out.append(f"| {basis} | {int(x['amp'] * 100)}% | {len(x['turns'])} | {diff or '完全相同'} |")
    return '\n'.join(out)


def lead_table(rows, prod, groups):
    peaks = sorted({r['price_peak'] for r in rows if r['product'] == prod})
    cos = []
    for r in rows:
        if r['product'] == prod and r['group'] in groups and r['company'] not in [c for c, _ in cos]:
            cos.append((r['company'], r['label']))
    head = '| 公司 | ' + ' | '.join(peaks) + ' |'
    out = [head, '|---|' + '---|' * len(peaks)]
    for c, label in cos:
        cells = []
        for pk in peaks:
            r = next((x for x in rows if x['product'] == prod and x['company'] == c and x['price_peak'] == pk), None)
            if not r or r['status'] != 'ok':
                cells.append('')
                continue
            lead = int(r['lead_months'])
            tag = '' if r['complete'] == 'True' else '*'
            if r['price_peak_confirmed'] != 'True':
                tag += '†'
            cells.append(f"{r['stock_peak']}（{lead:+d}）{tag}")
        out.append(f'| {label} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(out)


def multiple_table(rows, prod, groups):
    out = ['| 公司 | 价格峰 | 起点（最低月） | 股价峰 | 倍数（月末收盘） | 倍数（月内低→高） | 倍数（美元） | 基准 | 基准倍数 | 超额倍数 |',
           '|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        if r['product'] != prod or r['group'] not in groups or r['status'] != 'ok':
            continue
        f = lambda k, fmt='{:.1f}': (fmt.format(float(r[k])) if r.get(k) else '—')
        tags = ('' if r['complete'] == 'True' else '*') + ('‡' if r.get('upleg_truncated_by_listing') == 'True' else '')
        out.append(f"| {r['label']}{tags} | {r['price_peak']}{'' if r['price_peak_confirmed'] == 'True' else '†'} | "
                   f"{r['upleg_low']} | {r['stock_peak']} | {f('upleg_multiple')}x | {f('upleg_multiple_hl')}x | "
                   f"{f('upleg_multiple_usd')}x | {r.get('bench1') or '—'} | {f('bench1_multiple')}x | {f('excess1')}x"
                   + (f"（{r['bench2']} {f('excess2')}x）" if r.get('bench2') else '') + ' |')
    return '\n'.join(out)


def q3_block(s):
    names = {'main_pure': '主判定：纯存储组', 'sens_dram_makers_only': '敏感性：只含 DRAM 厂',
             'diversified_dram': '对照：多元化组（DRAM 周期）', 'nand_panel': '对照：NAND 公司（闪存周期）'}
    vmap = {'holds': '成立', 'falsified': '被推翻', 'inconclusive': '不确定'}
    out = ['| 样本 | 组合数 | 股价领先≥1个月的占比 | 领先月数中位数 | 领先3–9个月的占比 | 判定 |', '|---|---|---|---|---|---|']
    for k, v in s['q3'].items():
        if not v.get('n'):
            out.append(f'| {names[k]} | 0 | — | — | — | — |')
            continue
        out.append(f"| {names[k]} | {v['n']} | {v['share_lead_ge1'] * 100:.0f}% | {v['median_lead']:+g} | "
                   f"{v['share_lead_3_to_9'] * 100:.0f}% | {vmap[v['verdict']]} |")
    return '\n'.join(out)


def by_cycle(rows, prod='DRAM'):
    g = defaultdict(list)
    for r in rows:
        if (r['product'] == prod and r['group'] == 'pure' and r['status'] == 'ok' and r['complete'] == 'True'
                and r['price_peak_confirmed'] == 'True'):
            g[r['price_peak']].append(int(r['lead_months']))
    out = ['| 价格峰 | 公司数 | 领先月数（逐家） | 中位数 |', '|---|---|---|---|']
    for pk in sorted(g):
        v = g[pk]
        out.append(f"| {pk} | {len(v)} | {', '.join(f'{x:+d}' for x in v)} | {statistics.median(v):+g} |")
    return '\n'.join(out)


def main():
    s, rows, ph = load()
    print('## DRAM 价格周期日历（主规则：合同货币口径，门槛 20%）\n')
    print(calendar_table('DRAM', ph), '\n')
    print('### 敏感性\n')
    print(sensitivity_table(s, 'DRAM'), '\n')
    print('## NAND 价格周期日历（主规则）\n')
    print(calendar_table('NAND', ph), '\n')
    print('### 敏感性\n')
    print(sensitivity_table(s, 'NAND'), '\n')
    print('## Q3：股价峰和价格峰谁先\n')
    print(q3_block(s), '\n')
    print('### 按轮次（纯存储组，计入主判定的组合）\n')
    print(by_cycle(rows), '\n')
    print('### 逐家：股价峰月份（括号里是领先月数，正数 = 股价先见顶）\n')
    print('`*` 窗口内数据不完整，不计入判定；`†` 价格峰未确认（本轮），不计入判定。\n')
    print('**纯存储组 × DRAM 周期**\n')
    print(lead_table(rows, 'DRAM', {'pure'}), '\n')
    print('**多元化组 × DRAM 周期**\n')
    print(lead_table(rows, 'DRAM', {'diversified'}), '\n')
    print('**NAND 公司 × 闪存周期**\n')
    print(lead_table(rows, 'NAND', {'pure', 'diversified'}), '\n')
    print('## 上行倍数（月末收盘价口径）\n')
    print(multiple_table(rows, 'DRAM', {'pure', 'diversified'}), '\n')


if __name__ == '__main__':
    main()
