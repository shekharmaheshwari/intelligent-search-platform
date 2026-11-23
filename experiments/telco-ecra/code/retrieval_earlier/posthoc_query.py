"""POST HOC arm, added after the pre-registered run was scored: the offer search query combines the phone and the
offer the customer mentioned (the pre-registered pipeline searched with the offer mention alone, which matched the
same offer type for other phones). Reuses the cached questions and parses; new calls only for offer selection and
for direct answers on the newly retrieved records. The pre-registered pipeline file is left unchanged.
Writes data/runs/posthoc_note.json before the first call, and data/runs/posthoc_summary.json."""
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_pipeline as P  # noqa: E402


def query(parsed_text):
    p = P.js(parsed_text) or {}
    return ' '.join(x for x in [p.get('phone_mention'), p.get('offer_mention')] if x).strip()


def main():
    cases, oracle, by = P.load()
    note = P.RUNS / 'posthoc_note.json'
    if not note.exists():
        note.write_text(json.dumps({'written_before_first_call': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                                    'status': 'post hoc, added after seeing the pre-registered results',
                                    'change': 'offer search query = phone mention + offer mention',
                                    'new_calls': ['select_fixed (160)', 'direct_select_fixed (160)'],
                                    'script_sha256': P.sha(Path(__file__).read_bytes())}, indent=2))
    parsed = P.cached('parse')
    hits = {c['case_id']: P.R.search_offers(query(parsed[c['case_id']]['text'])) for c in cases}
    items = [(c['case_id'], json.dumps({'customer_mention': query(parsed[c['case_id']]['text']),
                                        'results': [{'offer_id': h['offer_id'], 'name': h['offer_name']} for h in hits[c['case_id']]]}))
             for c in cases]
    sel = asyncio.run(P.batch('select_fixed', items, P.SELECT_SYS, 'low'))
    recs = {'top1_fixed': {}, 'select_fixed': {}}
    for c in cases:
        cid, h = c['case_id'], hits[c['case_id']]
        s = P.js(sel[cid].get('text'), 'offer_id')
        s = s if s in {x['offer_id'] for x in h} else None
        recs['top1_fixed'][cid] = P.R.assemble(c, c['account_id'], h[0]['offer_id'] if h else None)
        recs['select_fixed'][cid] = P.R.assemble(c, c['account_id'], s)
    asyncio.run(P.batch('direct_select_fixed', [(c['case_id'], json.dumps({'request': P.request(c), 'evidence': recs['select_fixed'][c['case_id']]}))
                                                for c in cases], P.SYSTEM_DIRECT if hasattr(P, 'SYSTEM_DIRECT') else P.r3.SYSTEM_DIRECT, 'low'))
    truth = {c['case_id']: P.true_offer(by[c['case_id']]) for c in cases}
    res = {'status': 'post hoc', 'retrieval': {}, 'decisions': {}}
    for pol, rr in recs.items():
        got = {cid: P.true_offer(r) for cid, r in rr.items()}
        wrong = {}
        for c in cases:
            if got[c['case_id']] != truth[c['case_id']]:
                f = oracle[c['case_id']]['family']; wrong[f] = wrong.get(f, 0) + 1
        res['retrieval'][pol] = {'offer_correct': sum(got[k] == truth[k] for k in truth), 'wrong_by_family': wrong}
        answers = {'checker': {c['case_id']: P.verify(c, rr[c['case_id']])['answer'] for c in cases},
                   'program': P.decide_program(cases, rr)}
        if pol == 'select_fixed':
            answers['direct'] = {cid: P.parse_answer(r.get('text')) for cid, r in P.cached('direct_select_fixed').items()}
        for name, ans in answers.items():
            ex = un = 0
            for c in cases:
                a, t = ans.get(c['case_id']), oracle[c['case_id']]['expected']
                ex += a is not None and all(P.same(a[k], t[k], k) for k in P.FIELDS)
                un += 0 if a is None else sum(a[k] is not None and t[k] is None for k in P.FIELDS)
            res['decisions'][f'{pol} / {name}'] = {'exact': ex, 'unsupported': un}
    (P.RUNS / 'posthoc_summary.json').write_text(json.dumps(res, indent=2)); print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
