# TELCO-ECRA: Evidence-Consistent Reasoning Across Device SKUs, Billing Events, Promotions, and Customer-Support Responses

Shekhar Maheshwari, 2026. Accepted for publication at IEEE ICCA 2026.

- Paper (accepted version): to be added after the conference presentation
- IEEE Xplore: link and DOI to be added on publication
- Experiment code and data: [`experiments/telco-ecra/`](../../../experiments/telco-ecra/)

## Abstract
As more and more telecommunications service interactions integrate device catalogues, billing data, promotion rules,
and customer-support communications, there are frequently conflicts about the timing, language, or eligibility of
the data. TELCO-ECRA is offered as an evidence-consistent reasoning framework which brings together these sources
prior to the issuance of a response. The core question is where answering a telecom query directly with an LLM such
as GPT breaks down, mostly by giving an answer where the evidence doesn't back one up, and what architectural
mechanisms, evidence linking, time-aware checking, conflict handling and answers computed by programs from the
written rules, takes those errors away. On a custom-built synthetic benchmark of 1,000 instances (200 for practice,
800 for testing), generated from separate seeds after the rules, prompts and programs had been frozen, direct
GPT-5.4-mini answers reached 668 to 765 of 800 exact cases and still gave 52 to 200 unsupported answers where the
correct reply was "cannot tell", while programs that the model wrote from the same rules answered all 800. As the
expected answers come from the same written rules, this shows agreement with those rules, not accuracy on real
records. In a retrieval study among 10,000 distractor accounts, the program answered 683 of 800 cases with a search
on the offer alone and 756 with a search on phone and offer; with looser customer-style messages from a second
model, 595 of 800 no longer identified one offer, and asking the customer which offer is meant did best.

## How it relates to this repository
The benchmark, the model runs and the retrieval study are self-contained in the experiment archive; the retrieval
study stores its offers and catalogue in a separate Typesense instance, the search engine this platform uses, with
keyword and hybrid (keyword + embedding) search.

## Citation
```bibtex
@inproceedings{maheshwari2026telcoecra,
  author    = {Shekhar Maheshwari},
  title     = {{TELCO-ECRA}: Evidence-Consistent Reasoning Across Device {SKUs}, Billing Events, Promotions, and Customer-Support Responses},
  booktitle = {Proc. IEEE ICCA},
  year      = {2026}
}
```
