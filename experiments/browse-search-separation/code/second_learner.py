"""Second learner for the browse/search separation paper: gradient-boosted trees on the same features.

Runs Studies 1-3 with sklearn HistGradientBoostingClassifier in place of the production logistic regression,
keeping features, sample weights, splits, matched samples, and evaluation identical. Study 3 also adds a
row-matched item-level click-count formula. No model (LLM) calls: Study 3 replays cached rows.

Usage: python second_learner.py {study1|study2|study3}
Outputs: docs/research-papers/<PAPER_VERSION>/data/gbdt_*.{csv,json}
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'backend/src'))
sys.path.insert(0, str(HERE))
OUT = ROOT / 'docs/research-papers' / os.environ.get('PAPER_VERSION', 'srs-paper-humanized-v7')
GBDT = dict(max_iter=200, learning_rate=0.1, max_leaf_nodes=31, random_state=0)


def make_gbdt():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(**GBDT)


def fit_scores_gbdt(documents, rows, affinity_dims):
    """Same contract as popularity_model.fit_scores, with a GBDT estimator (dense input)."""
    from sklearn.feature_extraction import DictVectorizer
    from isp.services.domain.popularity_model import doc_features, position_weight
    features = {k: doc_features(d, affinity_dims) for k, d in documents.items()}
    usable = [r for r in rows if r['doc_id'] in features]
    if len(usable) < 5 or len({bool(r['clicked']) for r in usable}) < 2:
        return None
    vec = DictVectorizer(sparse=False)
    X = vec.fit_transform([features[r['doc_id']] for r in usable])
    if X.shape[1] == 0:
        return None
    model = make_gbdt()
    model.fit(X, [int(r['clicked']) for r in usable], sample_weight=[position_weight(r['rank']) for r in usable])
    ids = list(features)
    return dict(zip(ids, map(float, model.predict_proba(vec.transform([features[k] for k in ids]))[:, 1])))


def fit_combo_scores_gbdt(train, combo_features):
    from sklearn.feature_extraction import DictVectorizer
    agg = train.groupby(['combo', 'click'], observed=True).size().reset_index(name='w')
    if agg.click.nunique() < 2:
        return None
    vec = DictVectorizer(sparse=False); vec.fit(combo_features)
    model = make_gbdt()
    model.fit(vec.transform([combo_features[c] for c in agg.combo]), agg.click, sample_weight=agg.w)
    return model.predict_proba(vec.transform(combo_features))[:, 1]


def study1():
    sys.path.insert(0, str(ROOT / 'experiments/browse_search_separation'))
    import study
    study.fit_scores = fit_scores_gbdt
    out = OUT / 'data/gbdt_study1'
    study.run_study(out)
    import pandas as pd, numpy as np
    m = pd.read_csv(out / 'data/run_metrics.csv')
    m = m[(m.cohort == 'unseen') & (m.checkpoint == 20)]
    rng = np.random.default_rng(813); res = []
    for (n, s), g in m.groupby(['customers', 'scenario']):
        p = g.pivot(index='seed', columns='method', values='ndcg10')
        d = (p['browse'] - p['mixed_matched']).to_numpy()
        b = d[rng.integers(0, len(d), (5000, len(d)))].mean(axis=1)
        res.append({'customers': int(n), 'scenario': s, 'browse': float(p['browse'].mean()),
                    'matched': float(p['mixed_matched'].mean()), 'delta': float(d.mean()),
                    'ci': [float(x) for x in np.percentile(b, [2.5, 97.5])]})
    (OUT / 'data/gbdt_study1.json').write_text(json.dumps(res, indent=2)); print(json.dumps(res, indent=1))


def study2():
    import study_kuaisar as k
    k.fit_combo_scores = fit_combo_scores_gbdt
    out = OUT / 'data/gbdt_study2'; (out / 'data').mkdir(parents=True, exist_ok=True)
    rows, diffs, summary = k.run(out)
    (OUT / 'data/gbdt_study2.json').write_text(json.dumps({'metrics': rows, 'differences': diffs}, indent=2, default=float))
    import pandas as pd
    print(pd.DataFrame(diffs).to_string())


def study3():
    os.environ['OPENAI_API_KEY'] = 'cache-only'
    os.environ['PAPER_VERSION'] = 'browse-search-separation-v4'   # source of evals and cached rows
    import numpy as np
    import study_isp_agents as s
    import exposure_controls as ec
    from isp.services.domain.popularity_model import position_weight
    s.freeze_clock()
    docs, evals, rows = ec.load()
    rng = np.random.default_rng(2028)

    def formula(rows_):
        sc = dict.fromkeys(docs, 0.0)
        for r in rows_:
            sc[r['doc_id']] += position_weight(r['rank']) * (1 if r['clicked'] else .1)
        return sc

    learners = {'gbdt': lambda r: fit_scores_gbdt(docs, r, s.CONFIG['dims']), 'count_formula': formula}
    out, per = [], {}
    for lname, fit in learners.items():
        for arm in ec.ARMS:
            for cond in ['full', 'sparse_browse']:
                browse, pooled = ec.split(rows[arm], cond)
                d = ec.persona_delta(evals, docs, fit(browse),
                                     [fit(ec.matched(pooled, len(browse), k)) for k in range(s.CONFIG['matched_seeds'])])
                per[lname, arm, cond] = d
                out.append({'learner': lname, 'arm': arm, 'condition': cond, 'delta': float(d.mean()),
                            'ci': ec.boot_ci(d, rng)})
            print(lname, arm, 'done', flush=True)
        for arm in ['shuffledhead', 'randbrowse']:
            for cond in ['full', 'sparse_browse']:
                x = (per[lname, arm, cond] - per[lname, 'default', cond]).dropna()
                out.append({'learner': lname, 'arm': f'{arm} - default', 'condition': cond, 'delta': float(x.mean()),
                            'ci': ec.boot_ci(x, rng)})
    (OUT / 'data/gbdt_study3.json').write_text(json.dumps(out, indent=2))
    for r in out:
        print(f"{r['learner']:13s} {r['arm']:22s} {r['condition']:13s} {r['delta']:+.3f} [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]")


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True); (OUT / 'data').mkdir(exist_ok=True)
    {'study1': study1, 'study2': study2, 'study3': study3}[sys.argv[1]]()
