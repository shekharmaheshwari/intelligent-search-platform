"""Study 3 exposure controls for paper v3. No model calls: all rows are replayed from the response cache.

Arms (same personas, session plan, search sessions, and held-out grading sets):
  default        platform order, first three pages          -> logged policy
  shuffledhead   same first-three-pages pool, shuffled order -> ordering control (coverage held fixed)
  randbrowse     whole category, random order                -> wider exposure
Outputs (data/):
  exposure_arms.csv           browse-minus-matched per arm and condition, persona bootstrap
  exposure_volume_control.json randomized arm with browse rows subsampled to the default arm's count
  exposure_two_level.json     paired arm contrasts with training-persona and held-out-persona resampling
"""
import json
import os
from concurrent.futures import ProcessPoolExecutor
os.environ['OPENAI_API_KEY'] = 'cache-only'
import numpy as np
import pandas as pd
import study_isp_agents as s
from isp.services.domain.popularity_model import fit_scores

ARMS = {'default': '', 'shuffledhead': 'shuffledhead_', 'randbrowse': 'randbrowse_'}
CP = 20
D = s.OUT / 'data'


def load():
    docs = json.loads((s.CACHE / 'catalog.json').read_text())
    evals = json.loads((D / 'isp_evals.json').read_text())
    rows = {a: json.loads((s.CACHE / f'{t}isp_rows.json').read_text()) for a, t in ARMS.items()}
    return docs, evals, rows


def split(rows, cond, keep_browse_rows=None, seed=0):
    base = [r for r in rows if r['step'] < CP]
    browse = [r for r in base if r['query'] == '']
    search = [r for r in base if r['query'] != '']
    if cond == 'sparse_browse':  # identical rule to study_isp_agents.analyze
        rng = np.random.default_rng(CP)
        sess = sorted({r['session'] for r in browse})
        keep = set(rng.choice(sess, max(1, int(len(sess) * s.CONFIG['sparse_browse_fraction'])), replace=False))
        browse = [r for r in browse if r['session'] in keep]
    if keep_browse_rows is not None:  # volume control: drop whole browse sessions until the row count matches
        rng = np.random.default_rng(np.random.SeedSequence([seed, 55]))
        sess = list(rng.permutation(sorted({r['session'] for r in browse})))
        by = {}
        for r in browse:
            by.setdefault(r['session'], []).append(r)
        out = []
        for sid in sess:
            if len(out) + len(by[sid]) > keep_browse_rows:
                continue
            out += by[sid]
        browse = out
    return browse, browse + search


def matched(pooled, n, seed):
    rng = np.random.default_rng(np.random.SeedSequence([seed, CP, 884]))
    return [pooled[i] for i in np.sort(rng.choice(len(pooled), n, replace=False))]


def persona_delta(evals, docs, browse_sc, matched_scs):
    out = {}
    for e in evals:
        g = e['ratings']
        if max(g) == 0:
            continue
        b = s.ndcg10(np.array(g, float), np.array([browse_sc[c] for c in e['candidates']]))
        m = np.mean([s.ndcg10(np.array(g, float), np.array([sc[c] for c in e['candidates']])) for sc in matched_scs])
        out.setdefault(e['persona'], []).append(b - m)
    return pd.Series({p: np.mean(v) for p, v in out.items()})


def boot_ci(x, rng, n=5000):
    x = np.asarray(x)
    return [float(v) for v in np.percentile([rng.choice(x, len(x)).mean() for _ in range(n)], [2.5, 97.5])]


def replicate(args):
    """One two-level bootstrap replicate: shared training-persona and held-out-persona draws for every arm."""
    b, cond = args
    s.freeze_clock()
    docs, evals, rows = load()
    rng = np.random.default_rng(np.random.SeedSequence([b, 31]))
    train_ids = sorted({r['user'] for r in rows['default']})
    pick = rng.choice(train_ids, len(train_ids))
    held = sorted({e['persona'] for e in evals})
    hp = rng.choice(held, len(held))
    out = {}
    for arm in ARMS:
        by = {}
        for r in rows[arm]:
            by.setdefault(r['user'], []).append(r)
        boot = [{**r, 'session': f"{j}:{r['session']}"} for j, u in enumerate(pick) for r in by.get(u, [])]
        browse, pooled = split(boot, cond)
        d = persona_delta(evals, docs, fit_scores(docs, browse, s.CONFIG['dims']),
                          [fit_scores(docs, matched(pooled, len(browse), b), s.CONFIG['dims'])])
        out[arm] = float(np.mean([d[p] for p in hp if p in d]))
    return cond, out


def main():
    s.freeze_clock()
    docs, evals, rows = load()
    rng = np.random.default_rng(2027)
    table, per = [], {}
    for arm in ARMS:
        held = sorted({e['persona'] for e in evals})
        shown = {r['doc_id'] for r in rows[arm] if r['query'] == '' and r['step'] < CP}
        for cond in ['full', 'sparse_browse']:
            browse, pooled = split(rows[arm], cond)
            d = persona_delta(evals, docs, fit_scores(docs, browse, s.CONFIG['dims']),
                              [fit_scores(docs, matched(pooled, len(browse), k), s.CONFIG['dims'])
                               for k in range(s.CONFIG['matched_seeds'])])
            per[arm, cond] = d
            table.append({'arm': arm, 'condition': cond, 'browse_products_shown': len(shown),
                          'browse_rows': len(browse), 'delta': float(d.mean()), 'ci': boot_ci(d, rng),
                          'personas': len(d)})
    for arm in ['shuffledhead', 'randbrowse']:
        for cond in ['full', 'sparse_browse']:
            x = (per[arm, cond] - per['default', cond]).dropna()
            table.append({'arm': f'{arm} - default', 'condition': cond, 'delta': float(x.mean()), 'ci': boot_ci(x, rng)})
    pd.DataFrame(table).to_csv(D / 'exposure_arms.csv', index=False)
    print(pd.DataFrame(table).to_string(), flush=True)

    # Volume control: randomized arm with browse rows cut to the default arm's count, 20 subsample seeds.
    vol = {}
    for cond in ['full', 'sparse_browse']:
        target = len(split(rows['default'], cond)[0])
        ds = []
        for seed in range(20):
            browse, pooled = split(rows['randbrowse'], cond, keep_browse_rows=target, seed=seed)
            ds.append(persona_delta(evals, docs, fit_scores(docs, browse, s.CONFIG['dims']),
                                    [fit_scores(docs, matched(pooled, len(browse), seed), s.CONFIG['dims'])]))
        d = pd.concat(ds, axis=1).mean(axis=1)
        x = (d - per['default', cond]).dropna()
        vol[cond] = {'target_browse_rows': target, 'delta': float(d.mean()), 'ci': boot_ci(d, rng),
                     'change_vs_default': float(x.mean()), 'change_ci': boot_ci(x, rng)}
    (D / 'exposure_volume_control.json').write_text(json.dumps(vol, indent=2))
    print(json.dumps(vol, indent=2), flush=True)

    # Two-level bootstrap of the paired arm contrasts.
    jobs = [(b, cond) for cond in ['full', 'sparse_browse'] for b in range(200)]
    with ProcessPoolExecutor(max_workers=5) as ex:
        res = list(ex.map(replicate, jobs, chunksize=4))
    two = {}
    for cond in ['full', 'sparse_browse']:
        r = pd.DataFrame([o for c, o in res if c == cond])
        two[cond] = {f'{arm}': {'mean': float(r[arm].mean()), 'ci': [float(v) for v in np.percentile(r[arm], [2.5, 97.5])]}
                     for arm in ARMS}
        for arm in ['shuffledhead', 'randbrowse']:
            dd = r[arm] - r['default']
            two[cond][f'{arm} - default'] = {'mean': float(dd.mean()), 'ci': [float(v) for v in np.percentile(dd, [2.5, 97.5])]}
        two[cond]['replicates'] = len(r)
    (D / 'exposure_two_level.json').write_text(json.dumps(two, indent=2))
    print(json.dumps(two, indent=2))


if __name__ == '__main__':
    main()
