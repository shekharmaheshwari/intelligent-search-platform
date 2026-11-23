"""Synthetic retrieval world for the TELCO-ECRA retrieval study. Reads nothing from ISP.

1. An invented phone and plan catalogue (made-up brands, models and prices; near-duplicate names on purpose).
2. 1,000 target cases (200 practice, 800 test) built by the unchanged TELCO-ECRA case generator (generate.py) on that catalogue with a new
   seed, so the 20 scenario categories, the rules and the expected answers are exactly those of the paper.
3. Each target offer gets a customer-facing name; the 'missing offer' cases keep the name the customer asks about
   although no such offer exists.
4. Target accounts get extra billing events in neighbouring months (they don't change any expected answer, since
   only the account's service period counts).
5. Distractor accounts with their own billing events, support notes and offers, plus offers whose names resemble
   the target offers (same phone, other season, other plan, other segment), so retrieval has something to get wrong.

Writes data/world.json (all records), data/cases.json (target cases), data/oracle.json (expected answers),
data/catalog.json and data/design.json.
"""
import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEN = HERE.parents[1] / 'telco_ecra_operational' / 'generate.py'
OUT = HERE / 'data'
SEED = 20261013
N_DISTRACTOR_ACCOUNTS = 10000  # 25,000 exhausted the 270 distinct offer names of some phones
BRANDS = ['Aurel', 'Kestrel', 'Orbis', 'Velo', 'Lumen', 'Nimbus', 'Strata', 'Corvo']
MODELS = ['5', '7', '9', 'X', 'Edge', 'Air']
TIERS = ['', ' Plus', ' Pro', ' Pro Max']
PLAN_NAMES = ['Essential', 'Everyday', 'Unlimited', 'Unlimited Plus', 'Family Share', 'Value', 'Premium',
              'Starter', 'Connect', 'Go']
SEASONS = ['Spring', 'Summer', 'Autumn', 'Winter', 'Back-to-School', 'Holiday']
OFFER_KINDS = ['Trade-In Credit', 'Switcher Credit', 'Line-On Credit', 'Upgrade Credit', 'Loyalty Credit']
NAME_SUFFIXES = ['', ' Plus', ' Bonus', ' Deal', ' Reward', ' Extra', ' 2026', ' (Online)', ' (In-Store)']
SEGMENTS =['standard', 'employee', 'business', 'military', 'student']
STRIP = ['id', 'kind', 'case_id', 'account_id', 'synthetic', 'benchmark']


def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def catalog(rng):
    phones, plans = [], []
    for b in BRANDS:
        for m in rng.sample(MODELS, 3):
            for t in rng.sample(TIERS, rng.randrange(2, 5)):
                i = len(phones)
                phones.append({'id': f'PH-{i:03d}', 'title': f'{b} {m}{t}',
                               'skus': [str(rng.randrange(10 ** 11, 10 ** 12)) for _ in range(rng.randrange(1, 4))]})
    for i, name in enumerate(PLAN_NAMES):
        for variant in ['', ' 2.0']:
            lo = rng.choice([1, 1, 2, 3]); hi = lo + rng.randrange(2, 6)
            lines = list(range(lo, hi + 1)); base = rng.randrange(25, 90)
            plans.append({'id': f'PL-{len(plans):02d}', 'title': f'{name}{variant} ({lo}-{hi} lines)',
                          'eligible_lines': lines, 'line_prices': [base + 25 * k + rng.randrange(0, 6) for k in range(len(lines))]})
    return {'collections': {'Phones': {'rows': phones}, 'Plans': {'rows': plans}}}


def target_cases(cat):
    """Run the paper's own generator on the invented catalogue, unchanged except for paths, seed and labels."""
    (OUT / 'tmp').mkdir(parents=True, exist_ok=True)
    (OUT / 'tmp/catalog.json').write_text(json.dumps({**cat, 'snapshot_sha256': digest(cat)}))
    src = GEN.read_text()
    for a, b in [("OUT=ROOT/'docs/research-papers/telco-ecra-operational/data'", f"OUT=Path({str(OUT / 'tmp')!r})"),
                 ("SOURCE=ROOT/'docs/research-papers/telco-ecra/data/live_snapshot.json'", f"SOURCE=Path({str(OUT / 'tmp/catalog.json')!r})"),
                 ("random.Random(20261004)", f"random.Random({SEED})"), ("'seed':20261004", f"'seed':{SEED}"),
                 ("'telco-ecra-operational-v1'", "'telco-ecra-retrieval-1000-v1'"),
                 ("for replicate in range(10):", "for replicate in range(50):"),
                 ("ordinal=family_index*10+replicate", "ordinal=family_index*50+replicate"),
                 ("split='development' if replicate<2 else 'test'", "split='development' if replicate<10 else 'test'"),
                 ("origin='real ISP catalog fields copied into a synthetic scenario'", "origin='invented catalogue'"),
                 ("origin='real ISP catalog fields; plan_price output uses untyped source numeric units'", "origin='invented catalogue'")]:
        assert src.count(a) >= 1, a
        src = src.replace(a, b)
    ns = {'__name__': 'generate_world_impl', '__file__': str(GEN)}
    exec(compile(src, 'generate.py (retrieval world)', 'exec'), ns)
    ns['generate']()
    bench = json.loads((OUT / 'tmp/benchmark.json').read_text())
    oracle = json.loads((OUT / 'tmp/oracle.json').read_text())
    return bench, oracle


def main():
    rng = random.Random(SEED + 1)
    cat = catalog(rng)
    bench, oracle = target_cases(cat)
    phones = {p['id']: p for p in cat['collections']['Phones']['rows']}
    sku_phone = {s: p for p in phones.values() for s in p['skus']}
    plans = {p['id']: p for p in cat['collections']['Plans']['rows']}
    records = bench['records']
    by_case = {}
    for r in records:
        by_case.setdefault(r['case_id'], []).append(r)
    family = {o['case_id']: o['family'] for o in oracle}

    # 3. Customer-facing offer names; every target case has the name its customer would ask about.
    used = set()
    def offer_name(phone_title):
        for _ in range(1000):
            n = f'{phone_title} {rng.choice(SEASONS)} {rng.choice(OFFER_KINDS)}{rng.choice(NAME_SUFFIXES)}'
            if n not in used:
                used.add(n); return n
        raise RuntimeError(f'no unused offer name left for {phone_title}')
    for c in bench['cases']:
        recs = by_case[c['case_id']]
        acct = next(r for r in recs if r['kind'] == 'account')
        phone = sku_phone[acct['sku']]
        c['offer_name'] = offer_name(phone['title'])
        c['phone_title'] = phone['title']
        c['plan_title'] = plans[acct['plan_id']]['title']
        for r in recs:
            if r['kind'] == 'promotion':
                r['offer_name'] = c['offer_name']

    # 4. Neighbouring-month billing events on target accounts (outside the service period, so answers don't change).
    extra = []
    for c in bench['cases']:
        for k, period in enumerate(['2026-05', '2026-07']):
            amt = rng.randrange(60, 200)
            extra.append({'id': f"{c['case_id']}-x{k}", 'case_id': c['case_id'], 'account_id': c['account_id'],
                          'kind': 'billing', 'synthetic': True, 'benchmark': 'telco-ecra-retrieval-1000-v1',
                          'event_id': f"CHARGE-{c['account_id'].rsplit('-', 1)[-1]}-{period}", 'amount': str(amt), 'currency': 'USD',
                          'event_time': f'{period}-12T00:00:00+00:00', 'posted_at': f'{period}-13T00:00:00+00:00',
                          'service_period': period})
    records = records + extra

    # 5. Distractor accounts and look-alike offers.
    dist = []
    def add(**r):
        r.update(synthetic=True, benchmark='telco-ecra-retrieval-1000-v1'); dist.append(r); return r
    phone_list, plan_list = list(phones.values()), list(plans.values())
    months = ['2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08']
    for i in range(N_DISTRACTOR_ACCOUNTS):
        cid, aid = f'dst-{i:05d}', f'SYNTHETIC-DISTRACTOR-{i:05d}'
        ph, pl = rng.choice(phone_list), rng.choice(plan_list)
        period = rng.choice(months); day = rng.randrange(1, 28)
        cur = 'USD' if rng.random() < 0.95 else 'EUR'
        add(id=f'{cid}-acct', case_id=cid, account_id=aid, kind='account', sku=rng.choice(ph['skus']), plan_id=pl['id'],
            segment=rng.choice(SEGMENTS), lines=rng.choice(pl['eligible_lines']),
            activation_time=f'{period}-{day:02d}T00:00:00+00:00', currency=cur, service_period=period,
            ledger_complete_through=f'{period}-28T00:00:00+00:00')
        for k in range(rng.randrange(2, 6)):
            m = rng.choice(months)
            add(id=f'{cid}-b{k}', case_id=cid, account_id=aid, kind='billing', event_id=f'EV-{cid}-{k}',
                amount=str(rng.randrange(-40, 220)), currency=cur, event_time=f'{m}-{rng.randrange(1, 28):02d}T00:00:00+00:00',
                posted_at=f'{m}-{rng.randrange(1, 28):02d}T00:00:00+00:00', service_period=m)
        for k in range(rng.randrange(0, 3)):
            v = str(rng.randrange(40, 220))
            add(id=f'{cid}-s{k}', case_id=cid, account_id=aid, kind='support', statement=f'The net billed amount is {v} {cur}.',
                asserted_net=v, currency=cur, recorded_at=f'{rng.choice(months)}-{rng.randrange(1, 28):02d}T00:00:00+00:00',
                authority='non_authoritative_agent_note')
        if rng.random() < 0.3:
            add(id=f'{cid}-o', case_id=cid, account_id=aid, kind='promotion', offer_id=f'OFFER-{cid}', version=1,
                offer_name=offer_name(ph['title']), recorded_at='2026-02-01T00:00:00+00:00',
                valid_from=f'{period}-01T00:00:00+00:00', valid_to=f'{rng.choice(months)}-28T00:00:00+00:00',
                eligible_skus=[rng.choice(ph['skus'])], eligible_plans=[pl['id']], excluded_segments=['employee'],
                credit_amount=str(rng.randrange(10, 41)), currency=cur)
    # Look-alike offers for every target phone: same phone name, different season or kind (never the same name).
    for c in bench['cases']:
        acct = next(r for r in by_case[c['case_id']] if r['kind'] == 'account')
        for k in range(3):
            add(id=f"look-{c['case_id']}-{k}", case_id=f"look-{c['case_id']}", account_id=f"SYNTHETIC-LOOKALIKE-{c['case_id']}",
                kind='promotion', offer_id=f"OFFER-look-{c['case_id']}-{k}", version=1, offer_name=offer_name(c['phone_title']),
                recorded_at='2026-04-01T00:00:00+00:00', valid_from='2026-05-01T00:00:00+00:00',
                valid_to='2026-08-01T00:00:00+00:00', eligible_skus=[acct['sku']], eligible_plans=[acct['plan_id']],
                excluded_segments=['employee'], credit_amount=str(rng.randrange(10, 41)), currency='USD')
    world = records + dist
    import collections; dup = [k for k, v in collections.Counter(r['id'] for r in world).items() if v > 1]; assert not dup, dup[:5]

    cases = bench['cases']
    (OUT / 'world.json').write_text(json.dumps(world))
    (OUT / 'cases.json').write_text(json.dumps(cases, indent=1))
    (OUT / 'oracle.json').write_text(json.dumps(oracle, indent=1))
    (OUT / 'catalog.json').write_text(json.dumps(cat, indent=1))
    kinds = {}
    for r in world:
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    design = {'seed': SEED, 'target_cases': len(cases), 'test_cases': sum(c['split'] == 'test' for c in cases),
              'distractor_accounts': N_DISTRACTOR_ACCOUNTS, 'records': len(world), 'records_by_kind': kinds,
              'phones': len(phones), 'plans': len(plans), 'families': sorted(set(family.values())),
              'expected_answers': 'from the unchanged TELCO-ECRA generator; target records identical except added offer_name and neighbouring-month billing',
              'world_sha256': digest(world), 'catalog': 'invented; nothing read from ISP'}
    (OUT / 'design.json').write_text(json.dumps(design, indent=1))
    for f in (OUT / 'tmp').iterdir():
        f.unlink()
    (OUT / 'tmp').rmdir()
    print(json.dumps(design, indent=1))


if __name__ == '__main__':
    main()
