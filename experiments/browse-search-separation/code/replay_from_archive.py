"""Recompute Study 3 learned-policy results from the review archive (no catalog, no retrieval service, no model calls).

Usage: python code/replay_from_archive.py <archive_dir>
Prints browse-minus-matched NDCG@10 per arm and condition after 20 sessions, as in Tables VIII and X.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
from popularity_model import fit_scores
import popularity_model as pm

FROZEN_NOW = 1790439061 + 86400
pm.time = type('FrozenTime', (), {'time': staticmethod(lambda: FROZEN_NOW)})
DIMS = [{'field': 'category'}, {'field': 'manufacturer'}, {'field': 'price_tier', 'derived_from': 'price'}]


def ndcg10(g, s, k=10):
    order = np.lexsort((np.arange(len(s)), -np.asarray(s)))[:k]
    disc = 1 / np.log2(np.arange(k) + 2)
    ideal = np.dot(np.sort(g)[::-1][:k], disc)
    return float(np.dot(np.asarray(g)[order], disc) / ideal) if ideal > 0 else np.nan


def main(root):
    s3 = Path(root) / 'study3'
    feats = json.loads((s3 / 'item_features.json').read_text())
    docs = {k: {'category': v['category'], 'manufacturer': v['manufacturer'], 'metadata': {'price': v['price']},
                'indexed_at': 1790439061} for k, v in feats.items()}
    evals = json.loads((s3 / 'grading_sets.json').read_text())
    for arm in ['default_', 'shuffledhead_', 'randbrowse_']:
        rows = json.loads((s3 / f'{arm}rows.json').read_text())
        for cond in ['full', 'sparse_browse']:
            base = [r for r in rows if r['step'] < 20]
            browse = [r for r in base if r['query'] == '']
            if cond == 'sparse_browse':
                rng = np.random.default_rng(20)
                sess = sorted({r['session'] for r in browse})
                keep = set(rng.choice(sess, max(1, int(len(sess) * 0.1)), replace=False))
                browse = [r for r in browse if r['session'] in keep]
            pooled = browse + [r for r in base if r['query'] != '']
            sb = fit_scores(docs, browse, DIMS)
            sm = []
            for seed in range(20):
                rng = np.random.default_rng(np.random.SeedSequence([seed, 20, 884]))
                sm.append(fit_scores(docs, [pooled[i] for i in np.sort(rng.choice(len(pooled), len(browse), replace=False))], DIMS))
            d = {}
            for e in evals:
                g = np.array(e['ratings'], float)
                if g.max() == 0:
                    continue
                b = ndcg10(g, [sb[c] for c in e['candidates']])
                m = np.mean([ndcg10(g, [sc[c] for c in e['candidates']]) for sc in sm])
                d.setdefault(e['persona'], []).append(b - m)
            print(f"{arm.rstrip('_'):13s} {cond:14s} browse - matched = {np.mean([np.mean(v) for v in d.values()]):+.4f}")


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
