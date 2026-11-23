"""Load the synthetic world into SQLite, the study's stand-in for the operator's systems of record
(account, billing, CRM support notes, offer engine, product catalogue). Writes data/telco_ecra.sqlite."""
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / 'data/telco_ecra.sqlite'

SCHEMA = """
CREATE TABLE accounts (account_id TEXT PRIMARY KEY, sku TEXT, plan_id TEXT, segment TEXT, service_period TEXT, record TEXT);
CREATE TABLE billing_events (id TEXT PRIMARY KEY, account_id TEXT, service_period TEXT, event_id TEXT, record TEXT);
CREATE TABLE support_notes (id TEXT PRIMARY KEY, account_id TEXT, recorded_at TEXT, record TEXT);
CREATE TABLE offers (id TEXT PRIMARY KEY, offer_id TEXT, version INTEGER, offer_name TEXT, record TEXT);
CREATE TABLE phones (phone_id TEXT PRIMARY KEY, title TEXT, skus TEXT);
CREATE TABLE plans (plan_id TEXT PRIMARY KEY, title TEXT, eligible_lines TEXT, line_prices TEXT);
CREATE INDEX billing_account_period ON billing_events (account_id, service_period);
CREATE INDEX support_account ON support_notes (account_id);
CREATE INDEX offers_offer ON offers (offer_id);
"""


def main():
    world = json.loads((HERE / 'data/world.json').read_text())
    cat = json.loads((HERE / 'data/catalog.json').read_text())['collections']
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    n = {}
    for r in world:
        k, j = r['kind'], json.dumps(r)
        if k == 'account':
            con.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?)', (r['account_id'], r.get('sku'), r.get('plan_id'), r.get('segment'), r.get('service_period'), j))
        elif k == 'billing':
            con.execute('INSERT INTO billing_events VALUES (?,?,?,?,?)', (r['id'], r['account_id'], r.get('service_period'), r.get('event_id'), j))
        elif k == 'support':
            con.execute('INSERT INTO support_notes VALUES (?,?,?,?)', (r['id'], r['account_id'], r.get('recorded_at'), j))
        elif k == 'promotion':
            con.execute('INSERT INTO offers VALUES (?,?,?,?,?)', (r['id'], r.get('offer_id'), r.get('version'), r.get('offer_name'), j))
        elif k == 'catalog_link':
            continue  # per-case copies of catalogue rows; the catalogue itself is loaded below
        n[k] = n.get(k, 0) + 1
    for p in cat['Phones']['rows']:
        con.execute('INSERT INTO phones VALUES (?,?,?)', (p['id'], p['title'], json.dumps(p['skus'])))
    for p in cat['Plans']['rows']:
        con.execute('INSERT INTO plans VALUES (?,?,?,?)', (p['id'], p['title'], json.dumps(p['eligible_lines']), json.dumps(p['line_prices'])))
    con.commit()
    counts = {t: con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
              for t in ['accounts', 'billing_events', 'support_notes', 'offers', 'phones', 'plans']}
    con.close()
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
