"""Post-hoc analyses of Study B requested in review. No new model calls (dummy key; responses come from cache).

1. Random baseline: expected NDCG@10 of random ranking (50 draws per context).
2. Restricted re-scoring (tests the exposure explanation):
   a. browse-exposed: only graded products that appeared on a browse page in training;
   b. within-category: NDCG computed separately inside each category of a grading set.
3. Two-level bootstrap: resample training personas (and refit) and held-out personas together.
4. Bootstrap intervals for the per-category Spearman correlations (n = 8 categories).
"""
import asyncio
import json
import os
os.environ['OPENAI_API_KEY'] = 'cache-only'
import numpy as np
import pandas as pd
import study_isp_agents as s
from isp.services.domain.popularity_model import fit_scores

CFG = s.CONFIG
D = s.OUT / 'data'
CP = 20


def load_rows(docs):
    f = s.CACHE / 'isp_rows.json'
    if f.exists():
        return json.loads(f.read_text())
    llm = s.LLM(CFG)
    rows, _, _ = asyncio.run(s.simulate(CFG, llm, docs, s.make_personas(CFG)))
    assert llm.tokens['calls'] == 0, 'replay must not call the model'
    f.write_text(json.dumps(rows))
    return rows


def splits(rows, cond, rng_seed=0):
    base = [r for r in rows if r['step'] < CP]
    browse = [r for r in base if r['query'] == '']
    search = [r for r in base if r['query'] != '']
    if cond == 'sparse_browse':  # identical to analyze() in study_isp_agents
        srng = np.random.default_rng(CP)
        sess = sorted({r['session'] for r in browse})
        keep = set(srng.choice(sess, max(1, int(len(sess) * CFG['sparse_browse_fraction'])), replace=False))
        browse = [r for r in browse if r['session'] in keep]
    return browse, browse + search


def policies(docs, browse, pooled, seeds):
    out = {'browse': fit_scores(docs, browse, CFG['dims']), 'mixed': fit_scores(docs, pooled, CFG['dims'])}
    matched = []
    for seed in seeds:
        mrng = np.random.default_rng(np.random.SeedSequence([seed, CP, 884]))
        sample = [pooled[i] for i in np.sort(mrng.choice(len(pooled), len(browse), replace=False))]
        matched.append(fit_scores(docs, sample, CFG['dims']))
    return out, matched


def ndcg(gains, scores, k=10):
    return s.ndcg10(np.asarray(gains, float), np.asarray(scores, float), min(k, len(gains)))


def score_contexts(evals, docs, fitted, matched, mode, exposed):
    """Per-persona mean NDCG for each policy under one scoring mode."""
    rng = np.random.default_rng(77)
    res = []
    for e in evals:
        groups = []
        cand, gains = e['candidates'], e['ratings']
        if mode == 'all':
            groups = [list(range(len(cand)))]
        elif mode == 'browse_exposed':
            groups = [[i for i, c in enumerate(cand) if c in exposed]]
        elif mode == 'within_category':
            bycat = {}
            for i, c in enumerate(cand):
                bycat.setdefault(docs[c]['category'], []).append(i)
            groups = list(bycat.values())
        for g in groups:
            gg = [gains[i] for i in g]
            if len(g) < 2 or max(gg) == min(gg):
                continue  # no ranking information
            row = {'persona': e['persona']}
            for m, sc in fitted.items():
                row[m] = ndcg(gg, [sc[cand[i]] for i in g])
            row['mixed_matched'] = np.mean([ndcg(gg, [sc[cand[i]] for i in g]) for sc in matched])
            row['random'] = np.mean([ndcg(gg, rng.random(len(g))) for _ in range(50)])
            res.append(row)
    return pd.DataFrame(res).groupby('persona').mean()


def persona_ci(per, a, b, rng, n=5000):
    d = (per[a] - per[b]).to_numpy()
    boots = [rng.choice(d, len(d)).mean() for _ in range(n)]
    return float(d.mean()), *[float(x) for x in np.percentile(boots, [2.5, 97.5])], len(d)


def main():
    s.freeze_clock()
    docs = json.loads((s.CACHE / 'catalog.json').read_text())
    evals = json.loads((D / 'isp_evals.json').read_text())
    rows = load_rows(docs)
    exposed = {r['doc_id'] for r in rows if r['query'] == '' and r['step'] < CP}
    out = {'exposed_products': len(exposed)}
    rng = np.random.default_rng(2026)
    table = []
    for cond in ['full', 'sparse_browse']:
        browse, pooled = splits(rows, cond)
        fitted, matched = policies(docs, browse, pooled, range(CFG['matched_seeds']))
        for mode in ['all', 'browse_exposed', 'within_category']:
            per = score_contexts(evals, docs, fitted, matched, mode, exposed)
            for m in ['random', 'browse', 'mixed_matched', 'mixed']:
                table.append({'condition': cond, 'mode': mode, 'method': m, 'ndcg': float(per[m].mean()),
                              'personas': len(per)})
            for other in ['mixed_matched', 'mixed']:
                mean, lo, hi, n = persona_ci(per, 'browse', other, rng)
                table.append({'condition': cond, 'mode': mode, 'method': f'browse - {other}', 'ndcg': mean,
                              'ci_low': lo, 'ci_high': hi, 'personas': n})
        print('scored', cond, flush=True)
    pd.DataFrame(table).to_csv(D / 'isp_posthoc_rescoring.csv', index=False)

    # Two-level bootstrap: training personas (refit) x held-out personas. One matched seed per replicate.
    train_ids = sorted({r['user'] for r in rows})
    by_user = {u: [r for r in rows if r['user'] == u] for u in train_ids}
    held = sorted({e['persona'] for e in evals})
    brng = np.random.default_rng(31)
    two = {}
    for cond in ['full', 'sparse_browse']:
        deltas = []
        for b in range(200):
            pick = brng.choice(train_ids, len(train_ids))
            boot = []
            for j, u in enumerate(pick):  # duplicated personas become distinct sessions
                boot += [{**r, 'session': f"{j}:{r['session']}"} for r in by_user[u]]
            browse, pooled = splits(boot, cond)
            fitted, matched = policies(docs, browse, pooled, [b])
            hp_list = list(brng.choice(held, len(held)))
            ev = [e for p in hp_list for e in evals if e['persona'] == p]
            per = score_contexts(ev, docs, {'browse': fitted['browse']}, matched, 'all', exposed)
            deltas.append(float((per['browse'] - per['mixed_matched']).mean()))
        two[cond] = {'mean': float(np.mean(deltas)), 'ci': [float(x) for x in np.percentile(deltas, [2.5, 97.5])],
                     'replicates': len(deltas)}
        print('two-level', cond, two[cond], flush=True)
    out['two_level_bootstrap_browse_minus_matched'] = two

    # Spearman intervals over 8 categories: resample training sessions and grading contexts.
    e = pd.DataFrame([r for r in rows if r['step'] < CP])
    e['cat'] = e.doc_id.map(lambda d: docs[d]['category']); e['src'] = np.where(e['query'] == '', 'browse', 'search')
    g = pd.DataFrame([(i, docs[c]['category'], v) for i, x in enumerate(evals)
                      for c, v in zip(x['candidates'], x['ratings'])], columns=['ctx', 'cat', 'grade'])
    sess = e.session.unique(); ctxs = g.ctx.unique()
    agg = e.groupby(['session', 'src', 'cat']).clicked.agg(['sum', 'size']).reset_index()
    rhos = []
    for _ in range(2000):
        ws = pd.Series(np.bincount(brng.integers(0, len(sess), len(sess)), minlength=len(sess)), index=sess)
        wc = pd.Series(np.bincount(brng.integers(0, len(ctxs), len(ctxs)), minlength=len(ctxs)), index=ctxs)
        a = agg.assign(w=agg.session.map(ws))
        clk = (a['sum'] * a.w).groupby([a.src, a.cat]).sum(); imp = ((a['size'] - a['sum']) * a.w).groupby([a.src, a.cat]).sum()
        ctr = (clk / imp).unstack('src')
        gr = (g.grade * g.ctx.map(wc)).groupby(g.cat).sum() / g.ctx.map(wc).groupby(g.cat).sum()
        t = ctr.join(gr.rename('grade')).dropna()
        rhos.append([t.grade.corr(t.browse, method='spearman'), t.grade.corr(t.search, method='spearman'),
                     t.browse.corr(t.search, method='spearman'),
                     t.grade.corr(t.browse, method='spearman') - t.grade.corr(t.search, method='spearman')])
    r = np.array(rhos)
    names = ['grade_vs_browse_ctr', 'grade_vs_search_ctr', 'browse_vs_search_ctr', 'difference_browse_minus_search']
    out['spearman_ci_n8'] = {n: [float(x) for x in np.nanpercentile(r[:, i], [2.5, 97.5])] for i, n in enumerate(names)}
    (D / 'isp_posthoc.json').write_text(json.dumps(out, indent=2))
    print(pd.DataFrame(table).round(4).to_string()); print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
