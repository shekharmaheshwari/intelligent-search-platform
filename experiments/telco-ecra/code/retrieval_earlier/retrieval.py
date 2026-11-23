"""Retrieval tools for the TELCO-ECRA retrieval study. They return records exactly as stored and apply no rule.

Exact lookups (SQLite, the systems of record): get_account, get_billing_events, get_support_notes,
get_offer_versions, get_plan_link, get_phone_link.
Search (the study's own Typesense): search_offers, search_catalog.
`self_test` checks that exact retrieval reproduces every expected answer, and reports how often plain top-1 offer
search picks the wrong offer. No model is called.
"""
import json
import sqlite3
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from load_typesense import ts  # noqa: E402

_con = None


def con():
    global _con
    if _con is None:
        _con = sqlite3.connect(f"file:{HERE / 'data/telco_ecra.sqlite'}?mode=ro", uri=True)
    return _con


def _rows(sql, args):
    return [json.loads(r[0]) for r in con().execute(sql, args)]


def get_account(account_id):
    return _rows('SELECT record FROM accounts WHERE account_id=?', (account_id,))


def get_billing_events(account_id, period=None):
    if period is None:
        return _rows('SELECT record FROM billing_events WHERE account_id=? ORDER BY id', (account_id,))
    return _rows('SELECT record FROM billing_events WHERE account_id=? AND service_period=? ORDER BY id', (account_id, period))


def get_support_notes(account_id):
    return _rows('SELECT record FROM support_notes WHERE account_id=? ORDER BY id', (account_id,))


def get_offer_versions(offer_id):
    return _rows('SELECT record FROM offers WHERE offer_id=? ORDER BY version, id', (offer_id,))


def get_plan_link(plan_id):
    r = con().execute('SELECT plan_id, title, eligible_lines, line_prices FROM plans WHERE plan_id=?', (plan_id,)).fetchone()
    return [] if r is None else [{'id': f'cat-{r[0]}', 'kind': 'catalog_link', 'catalog_kind': 'plan', 'catalog_id': r[0], 'title': r[1],
                                  'eligible_lines': json.loads(r[2]), 'line_prices': json.loads(r[3])}]


def get_phone_link(sku):
    for pid, title, skus in con().execute('SELECT phone_id, title, skus FROM phones'):
        if sku in json.loads(skus):
            return [{'id': f'cat-{pid}', 'kind': 'catalog_link', 'catalog_kind': 'phone', 'catalog_id': pid, 'title': title, 'skus': json.loads(skus)}]
    return []


def search_offers(text, k=5):
    q = urllib.parse.urlencode({'q': text, 'query_by': 'offer_name,phones,plans', 'per_page': k,
                                'group_by': 'offer_id', 'group_limit': 1, 'prioritize_exact_match': 'true'})
    res = json.loads(ts('GET', f'/collections/te_offers/documents/search?{q}'))
    return [{'offer_id': g['group_key'][0], 'offer_name': g['hits'][0]['document']['offer_name'],
             'score': g['hits'][0]['text_match']} for g in res.get('grouped_hits', [])]


def search_catalog(text, kind=None, k=5):
    p = {'q': text, 'query_by': 'title', 'per_page': k}
    if kind:
        p['filter_by'] = f'catalog_kind:={kind}'
    res = json.loads(ts('GET', '/collections/te_catalog/documents/search?' + urllib.parse.urlencode(p)))
    return [h['document'] for h in res.get('hits', [])]


def assemble(case, account_id, offer_id, period=None):
    """Records for one question, as a real service would gather them, re-keyed to the asking account."""
    acct = get_account(account_id)
    recs = acct + get_billing_events(account_id, period) + get_support_notes(account_id)
    if offer_id:
        recs += get_offer_versions(offer_id)
    if acct:
        recs += get_plan_link(acct[0].get('plan_id')) + get_phone_link(acct[0].get('sku'))
    return [{**r, 'case_id': case['case_id'], 'account_id': account_id} for r in recs]


def self_test():
    sys.path.insert(0, str(HERE.parent / 'telco_ecra_operational'))
    from verifier import verify
    from score_model import same, FIELDS
    cases = json.loads((HERE / 'data/cases.json').read_text())
    oracle = {o['case_id']: o for o in json.loads((HERE / 'data/oracle.json').read_text())}
    exact = top1_right = 0
    wrong_examples = []
    for c in cases:
        hits = search_offers(c['offer_name'])
        exact_id = next((h['offer_id'] for h in hits if h['offer_name'] == c['offer_name']), None)
        recs = assemble(c, c['account_id'], exact_id)
        ans = verify(c, recs)['answer']
        exact += all(same(ans[k], oracle[c['case_id']]['expected'][k], k) for k in FIELDS)
        truth = exact_id
        top = hits[0]['offer_id'] if hits else None
        top1_right += top == truth
        if top != truth and len(wrong_examples) < 3:
            wrong_examples.append({'family': oracle[c['case_id']]['family'], 'asked': c['offer_name'],
                                   'top1': hits[0]['offer_name'] if hits else None})
    print(json.dumps({'cases': len(cases), 'exact_name_retrieval_then_checker_correct': exact,
                      'plain_top1_offer_search_matches_exact': top1_right, 'examples_of_top1_mismatch': wrong_examples}, indent=1))


if __name__ == '__main__':
    self_test()
