"""Post-hoc diagnostic for Study B: per-category exposure, CTR by surface, and held-out grades.
Replays the run from the response cache (a dummy key guarantees no new model calls)."""
import asyncio, json, os
os.environ['OPENAI_API_KEY'] = 'cache-only'
import numpy as np, pandas as pd
import study_isp_agents as s
D = s.OUT / 'data/'
docs = json.load(open(s.CACHE / 'catalog.json'))
ev = json.load(open(D / 'isp_evals.json'))
grades = pd.DataFrame([(docs[c]['category'], g) for e in ev for c, g in zip(e['candidates'], e['ratings'])], columns=['cat', 'grade'])
llm = s.LLM(s.CONFIG)
rows, _, _ = asyncio.run(s.simulate(s.CONFIG, llm, docs, s.make_personas(s.CONFIG)))
assert llm.tokens['calls'] == 0
e = pd.DataFrame(rows); e['cat'] = e.doc_id.map(lambda d: docs[d]['category'])
e['src'] = np.where(e['query'] == '', 'browse', 'search')
imp = e[~e.clicked].groupby(['cat', 'src']).size().unstack(fill_value=0)
clk = e[e.clicked].groupby(['cat', 'src']).size().unstack(fill_value=0)
t = pd.concat({'impressions': imp, 'ctr': (clk / imp).round(3)}, axis=1)
t['heldout_grade'] = grades.groupby('cat').grade.mean().round(2)
t = t.sort_values('heldout_grade', ascending=False)
out = {'single_category_browse_pages': float(e[e.src == 'browse'].groupby('session').cat.nunique().eq(1).mean()),
       'spearman_grade_vs_browse_ctr': float(t.heldout_grade.corr(t[('ctr', 'browse')], method='spearman')),
       'spearman_grade_vs_search_ctr': float(t.heldout_grade.corr(t[('ctr', 'search')], method='spearman'))}
t.to_csv(D / 'isp_category_diagnostic.csv'); (D / 'isp_category_diagnostic.json').write_text(json.dumps(out, indent=2))
print(t.to_string()); print(json.dumps(out, indent=2))

# Exposure coverage: how much of the catalog (items, manufacturers) each surface ever shows.
cov = {}
for src in ['browse', 'search']:
    x = e[(e.src == src) & ~e.clicked]
    cov[src] = {'distinct_items': int(x.doc_id.nunique()),
                'distinct_manufacturers': int(x.doc_id.map(lambda d: docs[d]['manufacturer']).nunique()),
                'impressions': int(len(x))}
evals_items = {c for v in ev for c in v['candidates']}
for src in cov:
    shown = set(e[e.src == src].doc_id)
    cov[src]['heldout_candidates_ever_shown'] = round(len(evals_items & shown) / len(evals_items), 3)
cov['catalog_items'] = len(docs); cov['catalog_manufacturers'] = len({d['manufacturer'] for d in docs.values()})
out['coverage'] = cov
(D / 'isp_category_diagnostic.json').write_text(json.dumps(out, indent=2))
print(json.dumps(cov, indent=2))
