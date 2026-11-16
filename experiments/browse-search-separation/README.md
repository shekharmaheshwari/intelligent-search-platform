# Browse/search separation: experiment data and code

Experiment archive for the paper *Separating Browse and Search Feedback in AI-Driven Learning-to-Rank Popularity:
An LLM Agent Approach* (S. Maheshwari, IEEE COMNETSAT 2026). The accepted paper is in
[`docs/papers/browse-search-separation/`](../../docs/papers/browse-search-separation/).

Study 3 ran on this platform's hybrid search service (Typesense BM25 + Voyage-3 embeddings, weighted RRF).

## Contents
- `code/`: all experiment code. `code/study1` reproduces Study 1 end to end. `code/popularity_model.py` is the
  production feature extractor and estimator used by every study. The Study 3 agent prompts are in
  `code/study_isp_agents.py`.
- `results/`: every metric file reported in the paper (per-unit metrics, paired differences, design files, hashes).
- `study3/`: Study 3 without the retail catalog. Product IDs are salted hashes; `item_features.json` keeps only the
  learner's inputs (category, manufacturer, price). `*rows.json` are the logged browse/search events of the three
  arms (default, shuffled head, randomized), `grading_sets.json` the held-out grades, `head_pools.json` the default
  arm's first-three-pages pools, and `model_responses.jsonl` every model response keyed by prompt hash.

Not included: the retail catalog (not redistributed) and the KuaiSAR dataset (download it from Zenodo, below).

## Reproduce
1. `pip install -r code/requirements.txt` (Python 3.14.7).
2. Study 1: `python code/study1/study.py && python code/study1/report.py`.
3. Study 2: download KuaiSAR-small (`KuaiSAR.zip`, doi:10.5281/zenodo.8181109, MD5 daea8cbf605db6bd5841740f0e4a12d9),
   unzip into `code/data_raw/`, then `python code/study_kuaisar.py`.
4. Study 3: `python code/replay_from_archive.py .` recomputes the browse-minus-matched contrasts of every exposure arm
   (paper Tables VIII and XI) from the archived rows and grades. All other reported numbers are in `results/`. The
   static random priority depends on product-ID order and differs from the paper; learned-policy numbers match.
   Re-running the agents requires the catalog and the retrieval service and is not possible from this archive.
5. Second learner (gradient-boosted trees, paper Table XII): `python code/second_learner.py study1` and
   `python code/second_learner.py study2` rerun Studies 1 and 2 (Study 2 needs KuaiSAR as in step 3). The Study 3
   tree and click-count results replay the cached agent sessions, which need the catalog; their outputs are in
   `results/gbdt_study3.json`, and all second-learner numbers are in `results/gbdt_study*.json`.

Expected output of step 4 (20 sessions, browse LR minus matched mixed LR, NDCG@10):

| Arm | Full browse | Sparse browse |
|---|---|---|
| Default order | −0.125 | +0.007 |
| Shuffled head | −0.102 | −0.004 |
| Whole category | +0.014 | +0.096 |
