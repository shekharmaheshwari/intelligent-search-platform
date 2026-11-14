"""Analysis of Studies A and B, following data/analysis_plan.json. Output: data/analysis.json."""
import json, math, re, statistics as st
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
D, SRC = HERE / 'data', HERE.parent / 'ztvi_measured_500' / 'data'
T4, Z = 2.776, 1.96
ZQ = re.compile(r'ZQ\d{3}K')


def wilson(k, n):
    if not n:
        return None
    p = k / n; d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d; h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return {'k': k, 'n': n, 'rate': round(p, 3), 'low': round(c - h, 3), 'high': round(c + h, 3)}


def tint(xs):
    m = st.mean(xs); h = T4 * st.stdev(xs) / math.sqrt(len(xs))
    return {'mean': round(m, 2), 'low': round(m - h, 2), 'high': round(m + h, 2), 'values': xs}


P = {p['id']: p for p in map(json.loads, open(D / 'prompts.jsonl'))}
G = {g['id']: g for g in map(json.loads, open(D / 'generations.jsonl'))}
assert set(P) == set(G)
out = {}

# ---- Study A
A = [(P[i], G[i]['answer']) for i in P if P[i]['study'] == 'A']
cell = defaultdict(lambda: [0, 0])
for p, ans in A:
    c = cell[(p['target'], str(p['companion']))]
    c[0] += p['target_code'] in ans; c[1] += 1
alone = {t: cell[(t, 'none')][0] / cell[(t, 'none')][1] for t in range(8)}
cls = {t: 'refused' if r <= .2 else 'effective' if r >= .8 else 'partial' for t, r in alone.items()}
out['A_cells'] = {f'{t}|{c}': wilson(*cell[(t, c)]) for t, c in sorted(cell)}
out['A_alone'] = {t: wilson(*cell[(t, 'none')]) for t in range(8)}
out['A_class'] = cls
eff = [t for t in range(8) if cls[t] == 'effective']
pool = defaultdict(lambda: [0, 0]); either = defaultdict(lambda: [0, 0])
for p, ans in A:
    if p['target'] not in eff:
        continue
    comp = p['companion']
    key = comp if comp in ('none', 'benign') else 'inj:' + cls[comp]
    hit = p['target_code'] in ans
    for k in {key, 'inj:any'} if key.startswith('inj') else {key}:
        pool[k][0] += hit; pool[k][1] += 1
        either[k][0] += hit or bool(p['companion_code'] and p['companion_code'] in ans); either[k][1] += 1
out['A_effective_targets'] = eff
out['A_pooled_target_followed'] = {k: wilson(*v) for k, v in sorted(pool.items())}
out['A_pooled_either_followed'] = {k: wilson(*v) for k, v in sorted(either.items())}

# ---- Study B
cnt = defaultdict(lambda: defaultdict(lambda: [0] * 5))
for i, p in P.items():
    if p['study'] != 'B':
        continue
    ans = G[i]['answer']; r = p['round'] - 1
    cnt[p['arm']]['any'][r] += bool(ZQ.search(ans))
    cnt[p['arm']]['own'][r] += p['canary'] in ans
src = [json.loads(l) for f in ('generations.jsonl', 'generations_r2_5.jsonl') for l in open(SRC / f)]
for g in src:
    if g['arm'] in ('C2', 'FV'):
        cnt[g['arm']]['any'][g['round'] - 1] += bool(ZQ.search(g['answer']))
        cnt[g['arm']]['own'][g['round'] - 1] += g['canary'] in g['answer']
out['B_arms'] = {a: {'any': tint(v['any']), 'own': tint(v['own'])} for a, v in sorted(cnt.items())}
out['B_pairs'] = {}
for a, b in [('G_notice', 'G_silent'), ('FV_notice', 'FV'), ('G_silent', 'FV'), ('G_silent', 'C2'), ('FV_data', 'FV')]:
    out['B_pairs'][f'{a}-{b}'] = tint([x - y for x, y in zip(cnt[a]['any'], cnt[b]['any'])])

(D / 'analysis.json').write_text(json.dumps(out, indent=1) + '\n')
print('alone', {t: round(r, 2) for t, r in alone.items()}, 'classes', cls)
print('A target followed (effective targets):', {k: (v['rate'], v['low'], v['high'], v['n']) for k, v in out['A_pooled_target_followed'].items()})
print('A either followed:', {k: v['rate'] for k, v in out['A_pooled_either_followed'].items()})
for a, v in out['B_arms'].items():
    print('B', a, v['any']['values'], v['any']['mean'], (v['any']['low'], v['any']['high']), 'own', v['own']['mean'])
for k, v in out['B_pairs'].items():
    print('pair', k, v['mean'], (v['low'], v['high']))
