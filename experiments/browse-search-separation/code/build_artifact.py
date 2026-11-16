"""Build the review archive for paper v3 (supplementary/browse_search_separation_artifact.zip).

Study 3 is released without the retail catalog: product IDs are replaced by salted hashes and only the
learner's inputs are kept (category, manufacturer, price). Logged slates, clicks, grades, splits, configs,
and model responses are included, so Tables VII-X can be recomputed with replay_from_archive.py without the
catalog text or the retrieval service. Rerunning the agents themselves requires the catalog and is not possible
from the archive. KuaiSAR is public and is downloaded separately (checksum in README).
"""
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PAPER = ROOT / 'docs/research-papers' / os.environ.get('PAPER_VERSION', 'browse-search-separation-v3')
STAGE = HERE / 'data_cache/artifact'
SALT = 'bss-v3'
ARMS = ['', 'shuffledhead_', 'randbrowse_']


def h(doc_id):
    return 'p' + hashlib.sha256((SALT + doc_id).encode()).hexdigest()[:12]


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / 'study3').mkdir(parents=True)
    cache = HERE / 'data_cache'
    docs = json.loads((cache / 'catalog.json').read_text())
    # The retailer's own house brand would identify the catalog; distinct neutral labels keep the encoding identical.
    # Labels are chosen to keep the alphabetical order of all manufacturer values, because the estimator's feature
    # column order (and hence its tolerance-limited solution) follows that order.
    house = {m: f't-house-{chr(97 + i)}' for i, m in enumerate(sorted({d['manufacturer'] for d in docs.values()
                                                                     if 'mobile' in d['manufacturer'].lower()}))}
    names = sorted({d['manufacturer'] for d in docs.values()})
    assert [house.get(m, m) for m in names] == sorted(house.get(m, m) for m in names), 'label order changed'
    feats = {h(k): {'category': d['category'], 'manufacturer': house.get(d['manufacturer'], d['manufacturer']),
                    'price': d['price']} for k, d in docs.items()}
    (STAGE / 'study3/item_features.json').write_text(json.dumps(feats, indent=0, sort_keys=True))
    for arm in ARMS:
        rows = json.loads((cache / f'{arm}isp_rows.json').read_text())
        for r in rows:
            r['doc_id'] = h(r['doc_id'])
        (STAGE / f'study3/{arm or "default_"}rows.json').write_text(json.dumps(rows))
    evals = json.loads((PAPER / 'data/isp_evals.json').read_text())
    for e in evals:
        e['candidates'] = [h(c) for c in e['candidates']]
    (STAGE / 'study3/grading_sets.json').write_text(json.dumps(evals))
    heads = json.loads((cache / 'head_pools.json').read_text())
    (STAGE / 'study3/head_pools.json').write_text(json.dumps({c: [h(x) for x in v] for c, v in heads.items()}))
    # Model responses only (keyed by prompt hash); prompts embed catalog text and are not released.
    shutil.copy(cache / 'llm_cache.jsonl', STAGE / 'study3/model_responses.jsonl')
    for f in ['isp_personas.json', 'isp_sessions.json', 'isp_design.json', 'isp_summary.json']:
        shutil.copy(PAPER / 'data' / f, STAGE / 'study3' / f)
    # Code and results.
    (STAGE / 'code').mkdir()
    for f in sorted(HERE.glob('*.py')) + [HERE / 'requirements.txt']:
        shutil.copy(f, STAGE / 'code' / f.name)
    shutil.copytree(ROOT / 'experiments/browse_search_separation', STAGE / 'code/study1',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy(ROOT / 'backend/src/isp/services/domain/popularity_model.py', STAGE / 'code/popularity_model.py')
    shutil.copytree(PAPER / 'data', STAGE / 'results', ignore=shutil.ignore_patterns('*isp_evals.json'))
    (STAGE / 'README.md').write_text(README)
    out = PAPER / 'supplementary' / os.environ.get('ARCHIVE_NAME', 'browse_search_separation_artifact.zip')
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(STAGE.rglob('*')):
            if f.is_file():
                z.write(f, f.relative_to(STAGE))
    print(out, round(out.stat().st_size / 1e6, 1), 'MB')


README = """# Browse/search separation: review archive

Contents
- code/: all experiment code. `code/study1` reproduces Study 1 end to end. `code/popularity_model.py` is the
  production feature extractor and estimator used by every study.
- results/: every metric file reported in the paper (per-unit metrics, paired differences, design files, hashes).
- study3/: Study 3 without the retail catalog. Product IDs are salted hashes; `item_features.json` keeps only the
  learner's inputs (category, manufacturer, price). `*rows.json` are the logged browse/search events of the three
  arms (default, shuffled head, randomized), `grading_sets.json` the held-out grades, `head_pools.json` the default
  arm's first-three-pages pools, and `model_responses.jsonl` every model response keyed by prompt hash.

Reproduce
1. `pip install -r code/requirements.txt` (Python 3.14.7).
2. Study 1: `python code/study1/study.py && python code/study1/report.py`.
3. Study 2: download KuaiSAR-small (`KuaiSAR.zip`, doi:10.5281/zenodo.8181109, MD5 daea8cbf605db6bd5841740f0e4a12d9),
   unzip into `code/data_raw/`, then `python code/study_kuaisar.py`.
4. Study 3: `python code/replay_from_archive.py .` recomputes the browse-minus-matched contrasts of every exposure arm
   (paper Tables VIII and XI) from the archived rows and grades. All other reported numbers are in results/. The static random priority depends on product-ID order and differs from the paper;
   learned-policy numbers match. Re-running the agents requires the catalog and the retrieval service and is not
   possible from this archive.
5. Second learner (gradient-boosted trees, paper Table XII): `python code/second_learner.py study1` and
   `python code/second_learner.py study2` rerun Studies 1 and 2 (Study 2 needs KuaiSAR as in step 3). The Study 3
   tree and click-count results replay the cached agent sessions, which need the catalog; their outputs are in
   `results/gbdt_study3.json`, and all second-learner numbers are in `results/gbdt_study*.json`.
"""

if __name__ == '__main__':
    main()
