"""Paper v4 additions for Study 3 (default arm). No model calls.

1. Session statistics excluding sessions whose listing or search returned no products.
2. Loss decomposition by exposure: products never shown on browse pages, split by whether browse ever showed
   their manufacturer, plus browse-shown products. Same models and grading sets as Table IX.
"""
import json
import os
os.environ['OPENAI_API_KEY'] = 'cache-only'
import numpy as np
import pandas as pd
import posthoc_study_b as p

s = p.s


def groups_by(evals, key):
    """Yield (persona, gains, candidate ids) for each grading set restricted by key(candidate)."""
    for e in evals:
        idx = [i for i, c in enumerate(e['candidates']) if key(c)]
        g = [e['ratings'][i] for i in idx]
        if len(idx) >= 2 and max(g) > min(g):
            yield e['persona'], g, [e['candidates'][i] for i in idx]


def score(evals, key, fitted, matched):
    res = {}
    for persona, g, cand in groups_by(evals, key):
        b = p.ndcg(g, [fitted['browse'][c] for c in cand])
        m = np.mean([p.ndcg(g, [sc[c] for c in cand]) for sc in matched])
        res.setdefault(persona, []).append((b, m, len(cand)))
    return res


def main():
    s.freeze_clock()
    docs = json.loads((s.CACHE / 'catalog.json').read_text())
    evals = json.loads((p.D / 'isp_evals.json').read_text())
    rows = json.loads((s.CACHE / 'isp_rows.json').read_text())
    out = {}

    sess = pd.DataFrame(json.loads((p.D / 'isp_sessions.json').read_text()))
    sess['type'] = np.where(sess['query'] == '', 'browse', 'search')
    ne = sess[sess.shown > 0]
    out['session_stats_nonempty'] = {
        t: {'sessions': int((ne.type == t).sum()), 'empty_excluded': int(((sess.type == t) & (sess.shown == 0)).sum()),
            'clicks_per_session': float(ne[ne.type == t].clicks.mean()),
            'zero_click_share': float((ne[ne.type == t].clicks == 0).mean()),
            'impressions_per_session': float(ne[ne.type == t].shown.mean())} for t in ['browse', 'search']}

    shown = {r['doc_id'] for r in rows if r['query'] == '' and r['step'] < p.CP}
    seen_mfr = {docs[d]['manufacturer'] for d in shown}
    out['browse_manufacturers_seen'] = len(seen_mfr)
    out['catalog_manufacturers'] = len({d['manufacturer'] for d in docs.values()})
    keys = {'browse_shown': lambda c: c in shown,
            'unshown_manufacturer_seen': lambda c: c not in shown and docs[c]['manufacturer'] in seen_mfr,
            'unshown_manufacturer_unseen': lambda c: c not in shown and docs[c]['manufacturer'] not in seen_mfr}
    rng = np.random.default_rng(4040)
    table = []
    for cond in ['full', 'sparse_browse']:
        browse, pooled = p.splits(rows, cond)
        fitted, matched = p.policies(docs, browse, pooled, range(s.CONFIG['matched_seeds']))
        for name, key in keys.items():
            res = score(evals, key, fitted, matched)
            per = pd.Series({k: np.mean([b - m for b, m, _ in v]) for k, v in res.items()})
            boots = [rng.choice(per.to_numpy(), len(per)).mean() for _ in range(5000)]
            n_groups = sum(len(v) for v in res.values())
            size = np.mean([n for v in res.values() for _, _, n in v]) if n_groups else 0
            table.append({'condition': cond, 'subset': name, 'delta': float(per.mean()),
                          'ci': [float(x) for x in np.percentile(boots, [2.5, 97.5])],
                          'personas': len(per), 'groups': n_groups, 'mean_items': float(size)})
    out['loss_by_exposure'] = table
    (p.D / 'isp_v4_posthoc.json').write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
