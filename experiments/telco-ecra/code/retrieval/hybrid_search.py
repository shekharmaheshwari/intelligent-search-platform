"""POST HOC arm: hybrid offer search (keyword + machine-learned embeddings) in place of keyword-only search.

Offer documents (offer name, phones, plans) and the parsed phone + offer mention of each message are embedded with
Voyage AI's voyage-3 model (1024 dimensions); Typesense fuses the keyword rank with the vector rank (rank fusion,
alpha = 0.3 on the vector side, the same weighting as the platform's own hybrid search). Everything else is unchanged:
the same cached messages and parses, the same selection prompt, the same checker and round-2 program.
Collection te1k_offers_hybrid lives in the separate TELCO-ECRA Typesense (port 8109), never in the platform's instance.
Writes data/runs/hybrid_note.json before the first call, embeddings to data/runs/emb_*.json, and
data/runs/hybrid_summary.json."""
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_pipeline as P  # noqa: E402
import ambiguity as A  # noqa: E402
from load_typesense import ts  # noqa: E402

MODEL, DIM, ALPHA, COLL = 'voyage-3', 1024, 0.3, 'te1k_offers_hybrid'
VKEY = os.environ.get('VOYAGE_API_KEY', '')


def embed(texts, kind):
    out = []
    for i in range(0, len(texts), 128):
        body = json.dumps({'input': texts[i:i + 128], 'model': MODEL, 'input_type': kind}).encode()
        req = urllib.request.Request('https://api.voyageai.com/v1/embeddings', body,
                                     {'Authorization': f'Bearer {VKEY}', 'Content-Type': 'application/json'})
        for attempt in range(5):
            try:
                d = json.loads(urllib.request.urlopen(req, timeout=60).read()); break
            except Exception as e:  # rate limit / transient
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))
        out += [x['embedding'] for x in sorted(d['data'], key=lambda x: x['index'])]
    return out


def cached_embed(name, texts, kind):
    f = P.RUNS / f'emb_{name}.json'
    have = json.loads(f.read_text()) if f.exists() else {}
    need = sorted({t for t in texts if t and t not in have})
    if need:
        have.update(zip(need, embed(need, kind)))
        f.write_text(json.dumps(have))
    return have


def doc_text(d):
    return f"{d['offer_name']}. Phones: {', '.join(d['phones'])}. Plans: {', '.join(d['plans'])}"


def build():
    docs = [json.loads(l) for l in ts('GET', '/collections/te1k_offers/documents/export').splitlines()]
    vec = cached_embed('offers', [doc_text(d) for d in docs], 'document')
    try:
        ts('DELETE', f'/collections/{COLL}')
    except Exception:
        pass
    ts('POST', '/collections', {'name': COLL, 'fields': [
        {'name': 'offer_id', 'type': 'string', 'facet': True}, {'name': 'offer_name', 'type': 'string'},
        {'name': 'version', 'type': 'int32'}, {'name': 'phones', 'type': 'string[]', 'facet': True},
        {'name': 'plans', 'type': 'string[]'}, {'name': 'embedding', 'type': 'float[]', 'num_dim': DIM}]})
    nd = '\n'.join(json.dumps({**d, 'embedding': vec[doc_text(d)]}) for d in docs)
    r = ts('POST', f'/collections/{COLL}/documents/import?action=create', nd, ndjson=True)
    bad = [l for l in r.splitlines() if '"success":true' not in l]
    print('indexed', len(docs) - len(bad), 'of', len(docs), 'errors', bad[:2])


def search(text, qvec, phone=None, k=5):
    s = {'collection': COLL, 'q': text or '*', 'query_by': 'offer_name,phones,plans', 'per_page': k,
         'group_by': 'offer_id', 'group_limit': 1, 'prioritize_exact_match': 'true',
         'vector_query': f"embedding:([{','.join(f'{x:.6f}' for x in qvec)}], k:200, alpha:{ALPHA})"}
    if phone:
        s['filter_by'] = f'phones:=[`{phone}`]'; s['query_by'] = 'offer_name'
    res = json.loads(ts('POST', '/multi_search', {'searches': [s]}))['results'][0]
    if 'error' in res:
        raise RuntimeError(res)
    return [{'offer_id': g['group_key'][0], 'offer_name': g['hits'][0]['document']['offer_name'],
             'score': g['hits'][0].get('hybrid_search_info', {}).get('rank_fusion_score', g['hits'][0].get('text_match'))}
            for g in res.get('grouped_hits', [])]


if __name__ == '__main__' and sys.argv[1:] == ['build']:
    build()


def author_questions():
    # Release copy: the 30 author-typed messages are archived in data/retrieval_earlier/author_messages.json
    msgs = json.loads((HERE.parents[1] / 'data/retrieval_earlier/author_messages.json').read_text())['messages']
    return {m['case_id']: {'text': json.dumps({'message': m['message']})} for m in msgs}


def run():
    cases_all, oracle, by = P.load()
    note = P.RUNS / 'hybrid_note.json'
    if not note.exists():
        note.write_text(json.dumps({'written_before_first_call': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                                    'status': 'post hoc, added after all keyword-search results were known',
                                    'change': f'offer search = Typesense hybrid (keyword + {MODEL} embeddings, rank fusion alpha {ALPHA}) '
                                              'instead of keyword only; query = phone mention + offer mention',
                                    'ask_when_tied': 'phone-filtered hybrid search on the offer mention; ask when the top two fused scores are equal',
                                    'new_calls': ['voyage-3 embeddings', 'select_hybrid_gpt (160)', 'select_hybrid_claude (160)', 'select_hybrid_author (30)'],
                                    'script_sha256': P.sha(Path(__file__).read_bytes())}, indent=2))
    sets = [('gpt', 'GPT-5.4-mini', P.cached('questions'), P.cached('parse')),
            ('claude', 'Claude Sonnet 5.5', P.cached('questions_claude'), P.cached('parse_claude'))]
    res = {'status': 'post hoc', 'model': MODEL, 'alpha': ALPHA, 'runs': []}
    for tag, label, questions, parsed in sets:
        cases = [c for c in cases_all if c['case_id'] in questions]
        classes = A.classify(cases, by, questions)
        mention = {c['case_id']: (P.js(parsed[c['case_id']].get('text')) or {}) for c in cases}
        qtext = {cid: ' '.join(x for x in [m.get('phone_mention'), m.get('offer_mention')] if x).strip() for cid, m in mention.items()}
        otext = {cid: (m.get('offer_mention') or '').strip() for cid, m in mention.items()}
        vec = cached_embed('queries', list(qtext.values()) + list(otext.values()), 'query')
        hits = {cid: (search(q, vec[q]) if q else []) for cid, q in qtext.items()}
        sc = asyncio.run(P.batch(f'select_hybrid_{tag}', [(cid, json.dumps({'customer_mention': qtext[cid],
                                 'results': [{'offer_id': h['offer_id'], 'name': h['offer_name']} for h in hits[cid]]})) for cid in qtext],
                                 P.SELECT_SYS, 'low'))
        picks = {'Top-1 hit': {}, 'Model picks': {}, 'Ask when tied': {}}
        asked = 0
        for c in cases:
            cid, h = c['case_id'], hits[c['case_id']]
            picks['Top-1 hit'][cid] = h[0]['offer_id'] if h else None
            s = P.js(sc[cid].get('text'), 'offer_id'); picks['Model picks'][cid] = s if s in {x['offer_id'] for x in h} else None
            acct = next(r for r in by[cid] if r['kind'] == 'account')
            phone = next(x['title'] for x in P.R.get_phone_link(acct['sku']))
            ph = search(otext[cid], vec[otext[cid]], phone=phone, k=10) if otext[cid] else []
            tie = len(ph) > 1 and ph[0]['score'] == ph[1]['score']
            asked += tie
            picks['Ask when tied'][cid] = None if (not ph or tie) else ph[0]['offer_id']
        for pol, picked in picks.items():
            recs = {c['case_id']: P.R.assemble(c, c['account_id'], picked[c['case_id']]) for c in cases}
            answers = {'checker': {c['case_id']: P.verify(c, recs[c['case_id']])['answer'] for c in cases}, 'program': P.decide_program(cases, recs)}
            for dec, ans in answers.items():
                row = {'messages': label, 'retrieval': pol, 'decision': dec, 'n': len(cases),
                       'right_offer': sum(picked[c['case_id']] == P.true_offer(by[c['case_id']]) for c in cases)}
                if pol == 'Ask when tied':
                    row['asked'] = asked
                ex = un = 0
                for c in cases:
                    a, t = ans.get(c['case_id']), oracle[c['case_id']]['expected']
                    ex += a is not None and all(P.same(a[k], t[k], k) for k in P.FIELDS)
                    un += 0 if a is None else sum(a[k] is not None and t[k] is None for k in P.FIELDS)
                row['exact'], row['unsupported'] = ex, un
                for group, members in [('identifiable', {'identifies', 'none_exists'}), ('not identifiable', {'ambiguous', 'misnamed'})]:
                    e2 = u2 = n = 0
                    for c in cases:
                        if classes[c['case_id']] not in members:
                            continue
                        n += 1
                        t = dict(oracle[c['case_id']]['expected'])
                        if group == 'not identifiable':
                            t['promotion_eligible'] = None
                        a = ans.get(c['case_id'])
                        e2 += a is not None and all(P.same(a[k], t[k], k) for k in P.FIELDS)
                        u2 += 0 if a is None else sum(a[k] is not None and t[k] is None for k in P.FIELDS)
                    row[group] = {'n': n, 'exact': e2, 'unsupported': u2}
                res['runs'].append(row)
    (P.RUNS / 'hybrid_summary.json').write_text(json.dumps(res, indent=2))
    for r in res['runs']:
        print(f"{r['messages']:18s} {r['retrieval']:13s} {r['decision']:8s} offer {r['right_offer']:3d}/{r['n']}  exact {r['exact']:3d} (unsup {r['unsupported']:2d})  "
              f"ident {r['identifiable']['exact']}/{r['identifiable']['n']} ({r['identifiable']['unsupported']})  "
              f"not ident {r['not identifiable']['exact']}/{r['not identifiable']['n']} ({r['not identifiable']['unsupported']})" + (f"  asked {r['asked']}" if 'asked' in r else ''))


if __name__ == '__main__' and sys.argv[1:] == ['run']:
    run()
