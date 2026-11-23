"""TELCO-ECRA retrieval study: free-text question -> understanding -> retrieval -> decision, on the 160 held-out
cases of the retrieval world. Requires OPENAI_API_KEY for the model steps.

Phases (each cached in data/runs/, so a run can resume and the analysis can be replayed without calls):
  preregister  write data/runs/preregistration.json before the first model call (refuses to overwrite)
  questions    GPT-5.4-mini writes one customer message per case from customer-visible facts only
  parse        GPT-5.4-mini extracts the offer and phone the customer mentions (the account comes from the session)
  select       GPT-5.4-mini picks the matching offer among the top-5 Typesense hits, or none
  direct       GPT-5.4-mini answers directly (round-2 prompt, low effort) from oracle and from retrieved records
  score        no calls: retrieval accuracy and exact cases for every retrieval x decision combination

Retrieval policies: 'oracle' (the case's own records), 'top1' (first offer hit), 'select' (model choice or none).
Decisions: the hand-written checker, the pre-registered round-2 program (run in an isolated interpreter), and
direct model answers.
Usage: python run_pipeline.py [phase ...]   (no argument = all phases in order)
"""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = HERE.parent / 'telco_ecra_operational'
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(OPS))
import retrieval as R  # noqa: E402
import run_baselines_v3 as r3  # noqa: E402  (frozen round-2 prompt, model and call wrapper)
from run_baselines_v2 import run_generated, parse as parse_answer  # noqa: E402
from score_model import same, FIELDS  # noqa: E402
from verifier import verify  # noqa: E402

DATA, RUNS = HERE / 'data', HERE / 'data/runs'
PROGRAM = HERE.parents[1] / 'docs/research-papers/telco-ecra-operational-v6/data/v3_code_run1.py'  # selected by the pre-registered rule
r3.LOG = RUNS / 'attempts.jsonl'

QUESTION_SYS = (
    "You write realistic customer messages for a synthetic telecom support benchmark. Given facts a customer would "
    "know about their own account, write the message they would type into a support chat, 1-3 sentences. The customer "
    "wants to know: whether they qualify for the offer they mention, what their net bill is for this month, whether "
    "what a support agent told them is right, and the price of their plan for their number of lines. Refer to the "
    "phone and the offer the way a real customer would: often shortened or slightly inexact, never with ids. Do not "
    "invent other facts and do not state any answer. Return JSON {\"message\": \"...\"}.")
PARSE_SYS = (
    "You read a customer's support message for a telecom operator. Extract only what the customer mentions. Return "
    "JSON {\"offer_mention\": the offer or promotion as the customer names it, or null; \"phone_mention\": the phone "
    "as the customer names it, or null}. Copy the customer's wording; do not guess.")
SELECT_SYS = (
    "A customer mentioned an offer. You are given the top search results from the operator's offer catalogue. Offers "
    "whose names differ in season, offer type or any suffix are different offers. Pick the result that is the same "
    "offer the customer means, or null if none of them clearly is. Return JSON {\"offer_id\": \"...\" or null}.")


def sha(x):
    return hashlib.sha256(x.encode() if isinstance(x, str) else x).hexdigest()


def load():
    cases = [c for c in json.loads((DATA / 'cases.json').read_text()) if c['split'] == 'test']
    oracle = {o['case_id']: o for o in json.loads((DATA / 'oracle.json').read_text())}
    world = json.loads((DATA / 'world.json').read_text())
    by = {}
    for r in world:
        by.setdefault(r['case_id'], []).append(r)
    return cases, oracle, by


def request(c):
    return {k: c[k] for k in ['case_id', 'account_id', 'event_time', 'knowledge_time', 'question']}


def customer_facts(c, recs):
    acct = next(r for r in recs if r['kind'] == 'account')
    sup = next((r for r in recs if r['kind'] == 'support'), None)
    facts = {'phone': c['phone_title'], 'offer_name': c['offer_name'], 'plan': c['plan_title'],
             'number_of_lines': acct.get('lines'), 'billing_month': acct.get('service_period')}
    if sup:
        facts['agent_said'] = sup.get('statement')
    return facts


def true_offer(recs):
    ids = {r['offer_id'] for r in recs if r['kind'] == 'promotion'}
    return ids.pop() if len(ids) == 1 else None


def preregister(cases):
    f = RUNS / 'preregistration.json'
    assert not f.exists(), 'pre-registration exists; refusing to overwrite'
    m = {'written_before_first_call': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'model_alias': r3.MODEL,
         'cases': len(cases), 'world_sha256': json.loads((DATA / 'design.json').read_text())['world_sha256'],
         'phases': ['questions (low effort, seed 1)', 'parse (low, seed 1)', 'select (low, seed 1)',
                    'direct answers on oracle, top1 and select records (round-2 prompt, low effort, seed 1)'],
         'retrieval': {'oracle': "the case's own records", 'top1': 'first offer_id from Typesense offer search',
                       'select': 'model choice among top-5 offer hits, or none'},
         'decisions': ['hand-written checker', 'pre-registered round-2 program v3_code_run1.py', 'direct GPT-5.4-mini'],
         'metrics': 'offer retrieval accuracy (null is correct for missing-offer cases); exact cases vs expected answers; '
                    'unsupported answers; every combination reported, run once',
         'sha256': {'question_sys': sha(QUESTION_SYS), 'parse_sys': sha(PARSE_SYS), 'select_sys': sha(SELECT_SYS),
                    'direct_sys': sha(r3.SYSTEM_DIRECT), 'program': sha(PROGRAM.read_bytes()),
                    'pipeline': sha(Path(__file__).read_bytes()), 'retrieval': sha((HERE / 'retrieval.py').read_bytes())}}
    f.write_text(json.dumps(m, indent=2)); print('pre-registered')


def cached(name):
    f = RUNS / f'{name}.jsonl'
    return {j['case_id']: j for j in map(json.loads, f.read_text().splitlines())} if f.exists() else {}


async def batch(name, items, system, effort):
    """items: [(case_id, user_text)]; calls only the uncached ones; appends to data/runs/<name>.jsonl."""
    from openai import AsyncOpenAI
    done = cached(name)
    todo = [(cid, u) for cid, u in items if cid not in done or done[cid].get('text') is None]
    if not todo:
        return cached(name)
    client = AsyncOpenAI(max_retries=0); sem = asyncio.Semaphore(4)
    res = await asyncio.gather(*[r3.call(client, system, u, 1, True, effort, sem, f'{name}:{cid}') for cid, u in todo])
    with (RUNS / f'{name}.jsonl').open('a') as f:
        for (cid, _), r in zip(todo, res):
            f.write(json.dumps({'case_id': cid, **r}) + '\n')
    print(name, 'calls', len(todo), 'failed', sum(r.get('text') is None for r in res), flush=True)
    return cached(name)


def js(t, key=None):
    try:
        d = json.loads(t)
        return d if key is None else d.get(key)
    except Exception:
        return None


def retrieve(cases, by, parsed, selected):
    out = {'oracle': {}, 'top1': {}, 'select': {}}
    hits_all = {}
    for c in cases:
        cid = c['case_id']
        p = js(parsed.get(cid, {}).get('text')) or {}
        mention = p.get('offer_mention') or p.get('phone_mention') or ''
        hits = R.search_offers(mention) if mention else []
        hits_all[cid] = hits
        sel = js(selected.get(cid, {}).get('text'), 'offer_id') if cid in selected else None
        sel = sel if sel in {h['offer_id'] for h in hits} else None
        out['oracle'][cid] = by[cid]
        out['top1'][cid] = R.assemble(c, c['account_id'], hits[0]['offer_id'] if hits else None)
        out['select'][cid] = R.assemble(c, c['account_id'], sel)
    return out, hits_all


def decide_program(cases, records):
    items = [{'case_id': c['case_id'], 'input': {'request': request(c), 'evidence': records[c['case_id']]}} for c in cases]
    res, err = run_generated(PROGRAM.read_text(), items)
    assert err is None, err
    return {cid: ({k: o.get(k) for k in FIELDS} if 'error' not in o else None) for cid, o in res.items()}


def main(phases):
    RUNS.mkdir(parents=True, exist_ok=True)
    cases, oracle, by = load()
    if 'preregister' in phases:
        preregister(cases)
    if 'questions' in phases:
        asyncio.run(batch('questions', [(c['case_id'], json.dumps(customer_facts(c, by[c['case_id']]))) for c in cases], QUESTION_SYS, 'low'))
    qs = cached('questions')
    if 'parse' in phases:
        asyncio.run(batch('parse', [(cid, js(q['text'], 'message') or '') for cid, q in qs.items()], PARSE_SYS, 'low'))
    parsed = cached('parse')
    if 'select' in phases:
        items = []
        for c in cases:
            p = js(parsed.get(c['case_id'], {}).get('text')) or {}
            mention = p.get('offer_mention') or p.get('phone_mention') or ''
            hits = R.search_offers(mention) if mention else []
            items.append((c['case_id'], json.dumps({'customer_mention': mention, 'results': [{'offer_id': h['offer_id'], 'name': h['offer_name']} for h in hits]})))
        asyncio.run(batch('select', items, SELECT_SYS, 'low'))
    selected = cached('select')
    recs, hits = retrieve(cases, by, parsed, selected)
    if 'direct' in phases:
        for pol in ['oracle', 'top1', 'select']:
            asyncio.run(batch(f'direct_{pol}', [(c['case_id'], json.dumps({'request': request(c), 'evidence': recs[pol][c['case_id']]})) for c in cases],
                              r3.SYSTEM_DIRECT, 'low'))
    if 'score' in phases:
        score(cases, oracle, by, recs, hits, selected)


def score(cases, oracle, by, recs, hits, selected):
    res = {'retrieval': {}, 'decisions': {}}
    truth = {c['case_id']: true_offer(by[c['case_id']]) for c in cases}
    for pol in ['top1', 'select']:
        got = {c['case_id']: true_offer(recs[pol][c['case_id']]) for c in cases}
        fam_wrong = {}
        for c in cases:
            if got[c['case_id']] != truth[c['case_id']]:
                f = oracle[c['case_id']]['family']; fam_wrong[f] = fam_wrong.get(f, 0) + 1
        res['retrieval'][pol] = {'offer_correct': sum(got[k] == truth[k] for k in truth), 'wrong_by_family': fam_wrong}
    for pol in ['oracle', 'top1', 'select']:
        answers = {'checker': {c['case_id']: verify(c, recs[pol][c['case_id']])['answer'] for c in cases},
                   'program': decide_program(cases, recs[pol])}
        d = cached(f'direct_{pol}')
        if d:
            answers['direct'] = {cid: parse_answer(r.get('text')) for cid, r in d.items()}
        for name, ans in answers.items():
            ex = un = 0
            for c in cases:
                a, t = ans.get(c['case_id']), oracle[c['case_id']]['expected']
                ex += a is not None and all(same(a[k], t[k], k) for k in FIELDS)
                un += 0 if a is None else sum(a[k] is not None and t[k] is None for k in FIELDS)
            res['decisions'][f'{pol} / {name}'] = {'exact': ex, 'unsupported': un}
    usage = {'in': 0, 'out': 0}
    for f in RUNS.glob('*.jsonl'):
        if f.name == 'attempts.jsonl':
            continue
        for j in map(json.loads, f.read_text().splitlines()):
            usage['in'] += (j.get('usage') or {}).get('in', 0); usage['out'] += (j.get('usage') or {}).get('out', 0)
    res['tokens'] = usage
    (RUNS / 'summary.json').write_text(json.dumps(res, indent=2)); print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main(sys.argv[1:] or ['preregister', 'questions', 'parse', 'select', 'direct', 'score'])
