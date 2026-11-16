"""Recompute Study 3 metrics for both arms from cached sessions with the frozen feature clock.
No model calls: a dummy key guarantees every response comes from the cache.

  default arm      -> data/isp_metrics.csv, isp_paired_differences.csv
  randomized arm   -> data/randbrowse_isp_metrics.csv, randbrowse_isp_paired_differences.csv, randbrowse_coverage.json
"""
import asyncio
import json
import os
import sys
os.environ['OPENAI_API_KEY'] = 'cache-only'
import study_isp_agents as s


def rows_for(arm, cfg, docs):
    f = s.CACHE / f'{arm}isp_rows.json'
    if f.exists():
        return json.loads(f.read_text())
    llm = s.LLM(cfg)
    rows, _, _ = asyncio.run(s.simulate(cfg, llm, docs, s.make_personas(cfg)))
    assert llm.tokens['calls'] == 0, 'replay must not call the model'
    f.write_text(json.dumps(rows))
    return rows


def main(arms):
    docs = json.loads((s.CACHE / 'catalog.json').read_text())
    evals = json.loads((s.OUT / 'data/isp_evals.json').read_text())  # identical grading sets in both arms
    for arm in arms:
        cfg = {**s.CONFIG, 'browse_order': 'random' if arm else 'default'}
        rows = rows_for(arm, cfg, docs)
        df = s.analyze(cfg, docs, rows, evals)
        diffs = s.paired(df, cfg)
        df.to_csv(s.OUT / f'data/{arm}isp_metrics.csv', index=False)
        diffs.to_csv(s.OUT / f'data/{arm}isp_paired_differences.csv', index=False)
        held = {c for e in evals for c in e['candidates']}
        shown = {r['doc_id'] for r in rows if r['query'] == '' and r['step'] < max(cfg['checkpoints'])}
        cov = {'browse_distinct_products': len(shown),
               'browse_distinct_manufacturers': len({docs[d]['manufacturer'] for d in shown}),
               'graded_products_ever_shown_on_browse': round(len(held & shown) / len(held), 3),
               'frozen_now': s.FROZEN_NOW}
        (s.OUT / f'data/{arm}isp_coverage.json').write_text(json.dumps(cov, indent=2))
        print(arm or 'default', cov); print(diffs.to_string())


if __name__ == '__main__':
    main(sys.argv[1:] or ['', 'randbrowse_'])
