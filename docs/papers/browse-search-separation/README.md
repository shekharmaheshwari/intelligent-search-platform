# Separating Browse and Search Feedback in AI-Driven Learning-to-Rank Popularity: An LLM Agent Approach

Shekhar Maheshwari, 2026. Accepted for publication at the IEEE International Conference on Communication, Networks and Satellite (COMNETSAT 2026).

- Paper (accepted version): to be added after the conference presentation
- IEEE Xplore: link and DOI to be added on publication
- Experiment code and data: [`experiments/browse-search-separation/`](../../../experiments/browse-search-separation/)

## Abstract
As more catalogue systems are logging browsing with empty queries and explicit-query search, there is a frequent
question of whether the two kinds of feedback should be learned together. A simple criterion is offered here: a
popularity ranker is trained only on browse rows instead of on pooled rows of the same amount. In a simulator built
to contain a constructed conflict among browse and search, browse-only training gains 0.106–0.424 NDCG@10, but loses
0.210 once a preference reversal is made. On the public KuaiSAR-small logs, browse-only training gives +0.0039 NDCG;
the gain goes up to +0.0225 when search rows dominate the pooled data. With LLM shopper agents that use a real retail
catalogue of 573 items, browse-only training loses by 0.125 NDCG@10. The default listings had only shown 185 products,
whereas with browse pages sampled from entire categories no browse-only disadvantage was detected; relative to the
default policy this is a reliable improvement of +0.130. With gradient-boosted trees the exposure effect and the
search-share trend carry over, the small full-data gain on the real logs does not. Separation therefore depended on
the make-up of the pooled data and on browse exposure; human users have yet to be tested.

## How it relates to this repository
Study 3 runs LLM shopper agents against this platform's search service in hybrid mode. Every study uses the
feature extraction and estimator of the platform's browse-popularity trainer
([`popularity_trainer.py`](../../../backend/src/isp/services/domain/popularity_trainer.py); a standalone copy is in the
archive's `code/popularity_model.py`). The rule tested is the trainer's empty-query filter (`query = ''`).

## Citation
```bibtex
@inproceedings{maheshwari2026browsesearch,
  author    = {Shekhar Maheshwari},
  title     = {Separating Browse and Search Feedback in {AI}-Driven Learning-to-Rank Popularity: An {LLM} Agent Approach},
  booktitle = {Proc. IEEE International Conference on Communication, Networks and Satellite (COMNETSAT)},
  year      = {2026}
}
```

## Copyright
© 2026 IEEE. Personal use of this material is permitted. Permission from IEEE must be obtained for all other uses, in
any current or future media, including reprinting/republishing this material for advertising or promotional purposes,
creating new collective works, for resale or redistribution to servers or lists, or reuse of any copyrighted component
of this work in other works.
