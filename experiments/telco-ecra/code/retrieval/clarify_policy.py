"""POST HOC prototype (no model calls): 'count candidates, then answer or ask'.

The account (and so its phone) is known from the session. Offers are searched for that phone only, with the
customer's offer wording as parsed earlier. If one offer scores strictly highest, it is used; if several tie at the
top score, the system asks which offer is meant (scored as "cannot tell" for eligibility); if none matches, it can't
tell. The decision step is unchanged (checker / round-2 program). Results are reported by the message classes of
ambiguity.py, which use a different rule (word matching on offer names), so the policy is not scored by itself.
Writes data/runs/clarify_summary.json."""
import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ambiguity as A  # noqa: E402
import run_pipeline as P  # noqa: E402
from load_typesense import ts  # noqa: E402


def phone_offers(mention, phone, k=10):
    p = {'q': mention or '*', 'query_by': 'offer_name', 'per_page': k, 'filter_by': f'phones:=[`{phone}`]',
         'group_by': 'offer_id', 'group_limit': 1}
    res = json.loads(ts('GET', '/collections/te1k_offers/documents/search?' + urllib.parse.urlencode(p)))
    return [(g['group_key'][0], g['hits'][0]['text_match'], g['hits'][0]['document']['offer_name']) for g in res.get('grouped_hits', [])]


def decide_offer(mention, phone):
    if not mention:
        return None, 'no mention'
    hits = phone_offers(mention, phone)
    if not hits:
        return None, 'no match'
    if len(hits) > 1 and hits[0][1] == hits[1][1]:
        return None, 'ask which offer'
    return hits[0][0], 'answer'


def main():
    cases, oracle, by = P.load()
    res = {'status': 'post hoc prototype', 'runs': []}
    for src, qc, pc in [('gpt', 'questions', 'parse'), ('claude', 'questions_claude', 'parse_claude')]:
        classes = A.classify(cases, by, P.cached(qc))
        parsed = P.cached(pc)
        recs, action = {}, {}
        for c in cases:
            p = P.js(parsed[c['case_id']].get('text')) or {}
            acct = next(r for r in by[c['case_id']] if r['kind'] == 'account')
            phone = next(x['title'] for x in P.R.get_phone_link(acct['sku']))
            oid, act = decide_offer(p.get('offer_mention') or '', phone)
            action[c['case_id']] = act
            recs[c['case_id']] = P.R.assemble(c, c['account_id'], oid)
        answers = {'checker': {c['case_id']: P.verify(c, recs[c['case_id']])['answer'] for c in cases},
                   'program': P.decide_program(cases, recs)}
        acts = {}
        for c in cases:
            g = 'answerable' if classes[c['case_id']] in ('identifies', 'none_exists') else 'not identifiable'
            acts.setdefault(g, {}).setdefault(action[c['case_id']], 0)
            acts[g][action[c['case_id']]] += 1
        for name, ans in answers.items():
            row = {'questions': src, 'decision': name, 'actions_by_class': acts}
            for group, members in [('answerable', {'identifies', 'none_exists'}), ('not identifiable', {'ambiguous', 'misnamed'})]:
                ex = un = n = 0
                for c in cases:
                    if classes[c['case_id']] not in members:
                        continue
                    n += 1
                    t = dict(oracle[c['case_id']]['expected'])
                    if group == 'not identifiable':
                        t['promotion_eligible'] = None
                    a = ans.get(c['case_id'])
                    ex += a is not None and all(P.same(a[k], t[k], k) for k in P.FIELDS)
                    un += 0 if a is None else sum(a[k] is not None and t[k] is None for k in P.FIELDS)
                row[group] = {'n': n, 'exact': ex, 'unsupported': un}
            res['runs'].append(row)
    (P.RUNS / 'clarify_summary.json').write_text(json.dumps(res, indent=2))
    for r in res['runs']:
        a, b = r['answerable'], r['not identifiable']
        print(f"{r['questions']:6s} {r['decision']:8s} answerable {a['exact']:3d}/{a['n']:3d} (unsup {a['unsupported']:2d})   "
              f"not identifiable {b['exact']:3d}/{b['n']:3d} (unsup {b['unsupported']:2d})")
    for src in ['gpt', 'claude']:
        print(src, 'actions:', next(r['actions_by_class'] for r in res['runs'] if r['questions'] == src))


if __name__ == '__main__':
    main()
