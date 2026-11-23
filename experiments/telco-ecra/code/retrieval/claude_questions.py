"""Second question source: Claude writes the customer messages, from the same customer-visible facts and with the
same instruction as GPT-5.4-mini did. The rest of the pipeline is unchanged (GPT-5.4-mini parses and picks offers,
Typesense searches, the checker / round-2 program / direct GPT-5.4-mini decide), so the comparison isolates the
question writer. Needs ANTHROPIC_API_KEY and OPENAI_API_KEY in the environment.
Writes data/runs/claude_note.json before the first call, caches every response, and data/runs/claude_summary.json."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_pipeline as P  # noqa: E402

CLAUDE_MODEL = 'claude-sonnet-5-5'
ENV = {'CLAUDE_Key': os.environ.get('ANTHROPIC_API_KEY', '')}


def strip(t):
    t = (t or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1].rsplit('```', 1)[0]
    return t


async def claude_questions(cases, by):
    import anthropic
    name = 'questions_claude'
    done = P.cached(name)
    todo = [c for c in cases if c['case_id'] not in done or done[c['case_id']].get('text') is None]
    if not todo:
        return P.cached(name)
    client = anthropic.AsyncAnthropic(api_key=ENV['CLAUDE_Key'].strip().strip('"\''), max_retries=0)
    sem = asyncio.Semaphore(8)

    async def one(c):
        user = json.dumps(P.customer_facts(c, by[c['case_id']]))
        async with sem:
            for attempt in range(6):
                t = time.time()
                try:
                    r = await client.messages.create(model=CLAUDE_MODEL, max_tokens=400, system=P.QUESTION_SYS,
                                                     messages=[{'role': 'user', 'content': user}])
                    text = strip(''.join(b.text for b in r.content if b.type == 'text'))
                    return {'case_id': c['case_id'], 'text': text, 'model': r.model, 'seconds': time.time() - t,
                            'usage': {'in': r.usage.input_tokens, 'out': r.usage.output_tokens}, 'attempts': attempt + 1}
                except Exception as e:
                    if attempt == 5:
                        return {'case_id': c['case_id'], 'text': None, 'error': repr(e)[:300], 'attempts': attempt + 1}
                    await asyncio.sleep(2 ** attempt + 1)

    res = await asyncio.gather(*[one(c) for c in todo])
    with (P.RUNS / f'{name}.jsonl').open('a') as f:
        for r in res:
            f.write(json.dumps(r) + '\n')
    print(name, 'calls', len(todo), 'failed', sum(r['text'] is None for r in res), flush=True)
    return P.cached(name)


def q_offer(t):
    p = P.js(t) or {}
    return p.get('offer_mention') or p.get('phone_mention') or ''


def q_both(t):
    p = P.js(t) or {}
    return ' '.join(x for x in [p.get('phone_mention'), p.get('offer_mention')] if x).strip()


def main():
    cases, oracle, by = P.load()
    note = P.RUNS / 'claude_note.json'
    if not note.exists():
        note.write_text(json.dumps({'written_before_first_call': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                                    'question_writer': CLAUDE_MODEL, 'instruction_sha256': P.sha(P.QUESTION_SYS),
                                    'pipeline': 'unchanged: GPT-5.4-mini parse and offer selection, Typesense search, checker / round-2 program / direct',
                                    'queries': ['offer mention only (pre-registered form)', 'phone + offer (post hoc form)'],
                                    'script_sha256': P.sha(Path(__file__).read_bytes())}, indent=2))
    qs = asyncio.run(claude_questions(cases, by))
    parsed = asyncio.run(P.batch('parse_claude', [(cid, P.js(q['text'], 'message') or '') for cid, q in qs.items()], P.PARSE_SYS, 'low'))
    truth = {c['case_id']: P.true_offer(by[c['case_id']]) for c in cases}
    res = {'question_writer': CLAUDE_MODEL, 'unparsable_questions': sum(P.js(q['text'], 'message') is None for q in qs.values()),
           'retrieval': {}, 'decisions': {}}
    for qname, qf in [('offer_only', q_offer), ('phone_offer', q_both)]:
        hits = {c['case_id']: P.R.search_offers(qf(parsed[c['case_id']]['text'])) if qf(parsed[c['case_id']]['text']) else [] for c in cases}
        items = [(c['case_id'], json.dumps({'customer_mention': qf(parsed[c['case_id']]['text']),
                                            'results': [{'offer_id': h['offer_id'], 'name': h['offer_name']} for h in hits[c['case_id']]]}))
                 for c in cases]
        sel = asyncio.run(P.batch(f'select_claude_{qname}', items, P.SELECT_SYS, 'low'))
        recs = {'top1': {}, 'select': {}}
        for c in cases:
            cid, h = c['case_id'], hits[c['case_id']]
            s = P.js(sel[cid].get('text'), 'offer_id')
            s = s if s in {x['offer_id'] for x in h} else None
            recs['top1'][cid] = P.R.assemble(c, c['account_id'], h[0]['offer_id'] if h else None)
            recs['select'][cid] = P.R.assemble(c, c['account_id'], s)
        if qname == 'phone_offer':
            asyncio.run(P.batch('direct_claude_phone_offer_select', [(c['case_id'], json.dumps({'request': P.request(c), 'evidence': recs['select'][c['case_id']]}))
                                                                      for c in cases], P.r3.SYSTEM_DIRECT, 'low'))
        for pol, rr in recs.items():
            key = f'{qname} / {pol}'
            got = {cid: P.true_offer(r) for cid, r in rr.items()}
            res['retrieval'][key] = {'offer_correct': sum(got[k] == truth[k] for k in truth)}
            answers = {'checker': {c['case_id']: P.verify(c, rr[c['case_id']])['answer'] for c in cases},
                       'program': P.decide_program(cases, rr)}
            if qname == 'phone_offer' and pol == 'select':
                answers['direct'] = {cid: P.parse_answer(r.get('text')) for cid, r in P.cached('direct_claude_phone_offer_select').items()}
            for name, ans in answers.items():
                ex = un = 0
                for c in cases:
                    a, t = ans.get(c['case_id']), oracle[c['case_id']]['expected']
                    ex += a is not None and all(P.same(a[k], t[k], k) for k in P.FIELDS)
                    un += 0 if a is None else sum(a[k] is not None and t[k] is None for k in P.FIELDS)
                res['decisions'][f'{key} / {name}'] = {'exact': ex, 'unsupported': un}
    (P.RUNS / 'claude_summary.json').write_text(json.dumps(res, indent=2)); print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
