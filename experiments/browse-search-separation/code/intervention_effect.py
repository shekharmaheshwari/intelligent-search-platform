"""Difference between arms (randomized minus default listing order) in browse-minus-matched NDCG@10.
Both arms share the 40 held-out personas and grading sets, so differences are paired by persona."""
import os
import json
import numpy as np
import pandas as pd
D = '../../docs/research-papers/' + os.environ.get('PAPER_VERSION', 'browse-search-separation-v2') + '/data/'
per = {}
for arm in ['', 'randbrowse_']:
    m = pd.read_csv(D + arm + 'isp_metrics.csv'); m = m[m.checkpoint == 20]
    g = m.groupby(['condition', 'method', 'persona']).ndcg10.mean()
    per[arm] = {c: (g[c, 'browse'] - g[c, 'mixed_matched']) for c in ['full', 'sparse_browse']}
rng = np.random.default_rng(404); out = {}
for c in ['full', 'sparse_browse']:
    d = (per['randbrowse_'][c] - per[''][c]).dropna().to_numpy()
    boots = [rng.choice(d, len(d)).mean() for _ in range(5000)]
    out[c] = {'did': float(d.mean()), 'ci': [float(x) for x in np.percentile(boots, [2.5, 97.5])], 'personas': len(d)}
json.dump(out, open(D + 'isp_intervention_effect.json', 'w'), indent=2); print(json.dumps(out, indent=2))
