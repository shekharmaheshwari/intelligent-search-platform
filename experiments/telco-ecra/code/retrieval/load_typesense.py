"""Load the searchable part of the world (offers and the phone/plan catalogue) into the study's own Typesense
(port 8109, see docker-compose.yml). Account, billing and support data stay in SQLite only."""
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = dict(l.split('=', 1) for l in (HERE / '.env').read_text().split() if '=' in l)
BASE = f"http://localhost:{ENV.get('TELCO_TS_PORT', '8109')}"
KEY = os.environ.get('TELCO_TS_API_KEY', ENV['TELCO_TS_API_KEY'])


def ts(method, path, body=None, ndjson=False):
    data = None
    if body is not None:
        data = (body if ndjson else json.dumps(body)).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'X-TYPESENSE-API-KEY': KEY, 'Content-Type': 'text/plain' if ndjson else 'application/json'})
    try:
        with urllib.request.urlopen(req) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        if method == 'DELETE' and e.code == 404:
            return ''
        raise RuntimeError(f'{method} {path}: {e.code} {e.read().decode()[:300]}')


def main():
    con = sqlite3.connect(HERE / 'data/telco_ecra.sqlite')
    phones = {r[0]: (r[1], json.loads(r[2])) for r in con.execute('SELECT phone_id, title, skus FROM phones')}
    sku_title = {s: t for t, skus in phones.values() for s in skus}
    plan_title = {r[0]: r[1] for r in con.execute('SELECT plan_id, title FROM plans')}

    ts('DELETE', '/collections/te1k_offers')
    ts('POST', '/collections', {'name': 'te1k_offers', 'fields': [
        {'name': 'offer_id', 'type': 'string', 'facet': True}, {'name': 'offer_name', 'type': 'string'},
        {'name': 'version', 'type': 'int32'}, {'name': 'phones', 'type': 'string[]'}, {'name': 'plans', 'type': 'string[]'}]})
    docs = []
    for rid, rec in con.execute('SELECT id, record FROM offers'):
        r = json.loads(rec)
        docs.append({'id': rid, 'offer_id': r['offer_id'], 'offer_name': r.get('offer_name') or '', 'version': int(r['version']),
                     'phones': sorted({sku_title.get(s, s) for s in r.get('eligible_skus', [])}),
                     'plans': sorted({plan_title.get(p, p) for p in r.get('eligible_plans', [])})})
    out = ts('POST', '/collections/te1k_offers/documents/import?action=create', '\n'.join(json.dumps(d) for d in docs), ndjson=True)
    bad_offers = sum('"success":false' in l for l in out.splitlines())

    ts('DELETE', '/collections/te1k_catalog')
    ts('POST', '/collections', {'name': 'te1k_catalog', 'fields': [
        {'name': 'catalog_id', 'type': 'string'}, {'name': 'catalog_kind', 'type': 'string', 'facet': True}, {'name': 'title', 'type': 'string'}]})
    cdocs = [{'id': k, 'catalog_id': k, 'catalog_kind': 'phone', 'title': t} for k, (t, _) in phones.items()]
    cdocs += [{'id': k, 'catalog_id': k, 'catalog_kind': 'plan', 'title': t} for k, t in plan_title.items()]
    out = ts('POST', '/collections/te1k_catalog/documents/import?action=create', '\n'.join(json.dumps(d) for d in cdocs), ndjson=True)
    bad_cat = sum('"success":false' in l for l in out.splitlines())
    counts = {c['name']: c['num_documents'] for c in json.loads(ts('GET', '/collections')) if c['name'].startswith('te_')}
    print(json.dumps({'collections': counts, 'failed_imports': bad_offers + bad_cat}))


if __name__ == '__main__':
    main()
