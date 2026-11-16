"""Study A: browse/search separation on real KuaiSAR logs (Sun et al., CIKM 2023).

Browse = recommendation-feed impressions (rec_inter); search = search-result
impressions (src_inter, clicked when click_cnt > 0). The learner is the
production estimator from popularity_model.make_estimator() over the same
categorical doc_features() path, with item category levels as dimensions.

Design fixed before inspecting outcomes:
  * temporal split: train 2023-05-22..05-28, evaluate 05-29..05-31
  * user split: 80% of users train, 20% held out (primary = unseen users)
  * browse evaluation units: feed requests (user_id, timestamp) with mixed labels
  * KuaiSAR has no displayed rank, so every row gets weight 1 (no position weight)
  * identical (features, label) rows are aggregated with summed sample weights,
    which leaves the logistic-regression objective unchanged
"""
from __future__ import annotations
import os
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/src'))
import numpy as np
import pandas as pd
from isp.services.domain.popularity_model import doc_features, make_estimator

HERE = Path(__file__).resolve().parent
RAW = HERE / 'data_raw/KuaiSAR_final'
CACHE = HERE / 'data_cache'
OUT = ROOT / 'docs/research-papers' / os.environ.get('PAPER_VERSION', 'browse-search-separation-v2')
CONFIG = {
    'dataset': 'KuaiSAR (Zenodo 10.5281/zenodo.8181109, KuaiSAR.zip md5 daea8cbf605db6bd5841740f0e4a12d9)',
    'train_days': ['2023-05-22', '2023-05-28'], 'test_days': ['2023-05-29', '2023-05-31'],
    'train_user_fraction': 0.8, 'user_split_seed': 7,
    'dims': [{'field': 'category'}, {'field': 'subcategory'}, {'field': 'item_type'}],
    'conditions': {'full': 1.0, 'sparse_browse': 0.1, 'sparse_constant_mix': 0.1,
                   'sparse_browse_1pct': 0.01, 'sparse_constant_mix_1pct': 0.01},
    # sparse_constant_mix keeps the same browse subsample as sparse_browse and also keeps the same fraction of
    # search rows, so the pooled search share is unchanged and only total volume falls.
    'methods': ['random', 'search', 'mixed', 'mixed_matched', 'browse'],  # random = exact expected NDCG of a uniform ordering
    'primary_comparison': 'browse minus mixed_matched; unseen users; NDCG on feed requests',
    'tie_breaks': 5, 'bootstrap': 2000, 'seed': 2026,
}


def load(raw=RAW, cache=CACHE):
    cache.mkdir(exist_ok=True)
    f = cache / 'events.parquet'
    if f.exists():
        return pd.read_parquet(f), pd.read_parquet(cache / 'items.parquet')
    rec = pd.read_csv(raw / 'rec_inter.csv', usecols=['user_id', 'item_id', 'timestamp', 'click', 'time'])
    rec = rec.rename(columns={'time': 'day'}).assign(day=lambda d: d.day.str[:10], source='browse',
                                                     request=lambda d: d.timestamp.astype('int64'))
    src = pd.read_csv(raw / 'src_inter.csv', usecols=['item_id', 'click_cnt', 'search_session_id', 'user_id',
                                                      'search_session_time'])
    src = pd.DataFrame({'user_id': src.user_id, 'item_id': src.item_id, 'click': (src.click_cnt > 0).astype('int8'),
                        'day': src.search_session_time.str[:10], 'source': 'search',
                        'request': src.search_session_id.astype('int64')})
    ev = pd.concat([rec[['user_id', 'item_id', 'click', 'day', 'source', 'request']], src], ignore_index=True)
    ev['click'] = ev.click.astype('int8'); ev['source'] = ev.source.astype('category')
    needed = set(ev.item_id.unique())
    parts = []
    for chunk in pd.read_csv(raw / 'item_features.csv', chunksize=2_000_000,
                             usecols=['item_id', 'item_type', 'first_level_category_name_en',
                                      'second_level_category_name_en']):
        parts.append(chunk[chunk.item_id.isin(needed)])
    items = pd.concat(parts).rename(columns={'first_level_category_name_en': 'category',
                                             'second_level_category_name_en': 'subcategory'})
    items = items.drop_duplicates('item_id').fillna('unknown')
    ev.to_parquet(f); items.to_parquet(cache / 'items.parquet')
    return ev, items


def item_feature_keys(items, dims):
    """Map item -> feature-combination id using the production doc_features()."""
    combos, keys = {}, {}
    for row in items[['item_id', 'category', 'subcategory', 'item_type']].drop_duplicates(
            ['category', 'subcategory', 'item_type']).itertuples(index=False):
        feats = doc_features({'category': row.category, 'subcategory': row.subcategory,
                              'item_type': row.item_type}, dims)
        combos[(row.category, row.subcategory, row.item_type)] = feats
    index = {k: i for i, k in enumerate(combos)}
    combo_id = items.set_index('item_id')[['category', 'subcategory', 'item_type']].apply(tuple, axis=1).map(index)
    return [combos[k] for k in combos], combo_id


def fit_combo_scores(train, combo_features):
    """Production estimator on aggregated rows; returns a score per feature combination."""
    from sklearn.feature_extraction import DictVectorizer
    agg = train.groupby(['combo', 'click'], observed=True).size().reset_index(name='w')
    if agg.click.nunique() < 2:
        return None
    vec = DictVectorizer(sparse=True)
    vec.fit(combo_features)
    model = make_estimator()
    model.fit(vec.transform([combo_features[c] for c in agg.combo]), agg.click, sample_weight=agg.w)
    return model.predict_proba(vec.transform(combo_features))[:, 1]


def page_ndcg(labels, scores, rng, breaks):
    """Mean NDCG over feed requests, averaging random tie-breaks (scores tie within a category)."""
    out = np.zeros(len(labels))
    for i, (y, s) in enumerate(zip(labels, scores)):
        disc = 1 / np.log2(np.arange(len(y)) + 2)
        ideal = np.dot(np.sort(y)[::-1], disc)
        vals = []
        for _ in range(breaks):
            order = np.lexsort((rng.random(len(y)), -s))
            vals.append(np.dot(y[order], disc) / ideal)
        out[i] = np.mean(vals)
    return out


def expected_random_ndcg(labels):
    """Each item's expected discount under a uniform permutation is the mean discount over positions."""
    out = np.zeros(len(labels))
    for i, y in enumerate(labels):
        disc = 1 / np.log2(np.arange(len(y)) + 2)
        out[i] = y.sum() * disc.mean() / np.dot(np.sort(y)[::-1], disc)
    return out


def run(out=OUT, cfg=CONFIG):
    start = time.time()
    ev, items = load()
    combo_features, combo_id = item_feature_keys(items, cfg['dims'])
    ev['combo'] = ev.item_id.map(combo_id).fillna(-1).astype('int64')
    ev = ev[ev.combo >= 0]
    users = np.sort(ev.user_id.unique())
    rng = np.random.default_rng(cfg['user_split_seed'])
    train_users = set(rng.choice(users, int(len(users) * cfg['train_user_fraction']), replace=False))
    ev['seen'] = ev.user_id.isin(train_users)
    lo, hi = cfg['train_days']
    train_all = ev[(ev.day >= lo) & (ev.day <= hi) & ev.seen]
    tlo, thi = cfg['test_days']
    test = ev[(ev.day >= tlo) & (ev.day <= thi) & (ev.source == 'browse')]

    # Requests with at least one click and one non-click are informative for ranking.
    stats = test.groupby(['user_id', 'request']).click.agg(['sum', 'size'])
    keep = stats[(stats['sum'] > 0) & (stats['sum'] < stats['size'])].index
    test = test.set_index(['user_id', 'request']).loc[keep].reset_index()
    groups = test.groupby(['user_id', 'request'], sort=False)
    page_user = np.array([k[0] for k in groups.groups])
    page_seen = np.array([u in train_users for u in page_user])
    page_labels = [g.click.to_numpy(float) for _, g in groups]
    page_combos = [g.combo.to_numpy() for _, g in groups]

    summary = {'browse_train_rows_full': int((train_all.source == 'browse').sum()),
               'search_train_rows': int((train_all.source == 'search').sum()),
               'test_requests': len(page_labels), 'test_requests_unseen': int((~page_seen).sum()),
               'feature_combinations': len(combo_features)}
    # Intent conflict in real data: per-category click rate in browse vs search.
    cat = train_all.merge(items[['item_id', 'category']], on='item_id')
    ctr = cat.groupby(['category', 'source'], observed=True).click.agg(['mean', 'size']).unstack('source')
    ctr = ctr[(ctr['size'] >= 1000).all(axis=1)]
    summary['category_ctr_spearman_browse_vs_search'] = float(
        ctr['mean']['browse'].rank().corr(ctr['mean']['search'].rank()))
    summary['categories_compared'] = len(ctr)
    ctr['mean'].to_csv(out / 'data/kuaisar_category_ctr.csv')
    # User-cluster bootstrap interval for that correlation: resample users, recompute per-category CTRs.
    cats = list(ctr.index)
    per_user = (cat[cat.category.isin(cats)].groupby(['user_id', 'source', 'category'], observed=True).click
                .agg(['sum', 'size']).unstack(['source', 'category'], fill_value=0))
    clicks = {s: per_user['sum'][s][cats].to_numpy() for s in ['browse', 'search']}
    shows = {s: per_user['size'][s][cats].to_numpy() for s in ['browse', 'search']}
    brng = np.random.default_rng(cfg['seed'] + 3); rhos = []
    for _ in range(cfg['bootstrap']):
        w = np.bincount(brng.integers(0, len(per_user), len(per_user)), minlength=len(per_user))
        rate = {s: (w @ clicks[s]) / np.maximum(w @ shows[s], 1) for s in clicks}
        rhos.append(pd.Series(rate['browse']).rank().corr(pd.Series(rate['search']).rank()))
    summary['category_ctr_spearman_ci'] = [float(x) for x in np.percentile(rhos, [2.5, 97.5])]

    rows, per_page = [], {}
    for cond, frac in cfg['conditions'].items():
        crng = np.random.default_rng(np.random.SeedSequence([cfg['seed'], int(frac * 1000)]))
        browse = train_all[train_all.source == 'browse']
        if frac < 1:
            browse = browse.iloc[np.sort(crng.choice(len(browse), int(len(browse) * frac), replace=False))]
        search = train_all[train_all.source == 'search']
        if cond.startswith('sparse_constant_mix'):
            srng = np.random.default_rng(np.random.SeedSequence([cfg['seed'], 77]))
            search = search.iloc[np.sort(srng.choice(len(search), int(len(search) * frac), replace=False))]
        mixed = pd.concat([browse, search])
        matched = mixed.iloc[np.sort(crng.choice(len(mixed), len(browse), replace=False))]
        sets = {'search': search, 'mixed': mixed, 'mixed_matched': matched, 'browse': browse}
        # Random priority uses no training data, so it is drawn once and shared by every condition.
        scores = {'random': np.random.default_rng(np.random.SeedSequence([cfg['seed'], 1])).random(len(combo_features))}
        for m, data in sets.items():
            scores[m] = fit_combo_scores(data, combo_features)
        for m in cfg['methods']:
            trng = np.random.default_rng(np.random.SeedSequence([cfg['seed'], 99]))  # same tie-breaks per method
            if m == 'random':  # exact expectation over uniformly random orderings of each request
                nd = expected_random_ndcg(page_labels)
            else:
                nd = page_ndcg(page_labels, [scores[m][c] for c in page_combos], trng, cfg['tie_breaks'])
            per_page[cond, m] = nd
            for cohort, mask in [('unseen', ~page_seen), ('seen', page_seen)]:
                rows.append({'condition': cond, 'method': m, 'cohort': cohort, 'ndcg': float(nd[mask].mean()),
                             'requests': int(mask.sum()), 'train_rows': int(len(sets[m])) if m in sets else 0})
        print(f'finished {cond}', flush=True)

    # Paired differences with user-cluster bootstrap (requests within a user are not independent).
    brng = np.random.default_rng(cfg['seed'])
    diffs = []
    for cond in cfg['conditions']:
        for cohort, mask in [('unseen', ~page_seen), ('seen', page_seen)]:
            for other in ['mixed_matched', 'mixed']:
                d = per_page[cond, 'browse'][mask] - per_page[cond, other][mask]
                u = page_user[mask]
                codes, uniq = pd.factorize(u)
                sums = np.bincount(codes, weights=d); counts = np.bincount(codes)
                boots = []
                for _ in range(cfg['bootstrap']):
                    pick = brng.integers(0, len(uniq), len(uniq))
                    boots.append(sums[pick].sum() / counts[pick].sum())
                lo_, hi_ = np.percentile(boots, [2.5, 97.5])
                diffs.append({'condition': cond, 'cohort': cohort, 'comparison': f'browse - {other}',
                              'delta': float(d.mean()), 'ci_low': float(lo_), 'ci_high': float(hi_),
                              'users': len(uniq)})
    pd.DataFrame(rows).to_csv(out / 'data/kuaisar_metrics.csv', index=False)
    pd.DataFrame(diffs).to_csv(out / 'data/kuaisar_paired_differences.csv', index=False)
    import sklearn
    summary.update({'runtime_seconds': time.time() - start, 'python': platform.python_version(),
                    'numpy': np.__version__, 'pandas': pd.__version__, 'scikit_learn': sklearn.__version__,
                    'design_sha256': hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(),
                    'core_sha256': hashlib.sha256((ROOT / 'backend/src/isp/services/domain/popularity_model.py')
                                                  .read_bytes()).hexdigest(),
                    'study_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    (out / 'data/kuaisar_design.json').write_text(json.dumps(cfg, indent=2))
    (out / 'data/kuaisar_summary.json').write_text(json.dumps(summary, indent=2))
    return rows, diffs, summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--out', type=Path, default=OUT)
    a = p.parse_args(); (a.out / 'data').mkdir(parents=True, exist_ok=True)
    rows, diffs, summary = run(a.out)
    print(json.dumps(summary, indent=2)); print(pd.DataFrame(diffs).to_string())
