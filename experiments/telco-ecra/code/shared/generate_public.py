"""Public fresh variant for paper v3: the same generator logic with (a) every copied catalog field replaced by a
synthetic stand-in and (b) a new seed, so the cases are fresh. Writes data/public/ in the v3 paper folder.
The catalog stand-in keeps the structure the generator needs (phone id/title/SKUs; plan id/title/eligible line
counts/line prices) but no real identifiers, titles, or prices."""
import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REAL = ROOT / 'docs/research-papers/telco-ecra/data/live_snapshot.json'
OUT = ROOT / 'docs/research-papers/telco-ecra-operational-v3/data/public'
SEED = 20261005


def synthetic_snapshot():
    real = json.loads(REAL.read_text())
    rng = random.Random(SEED + 1)
    as_list = lambda v: v if isinstance(v, list) else json.loads(str(v).replace("'", '"'))
    same_type = lambda orig, new: new if isinstance(orig, list) else str(new)
    phones = [{'id': f'SYN-PHONE-{i:03d}', 'title': f'Synthetic Phone {i:03d}',
               'skus': same_type(p['skus'], [str(rng.randrange(10**11, 10**12)) for _ in as_list(p['skus'])])}
              for i, p in enumerate(real['collections']['Phones']['rows'])]
    plans = []
    for i, p in enumerate(real['collections']['Plans']['rows']):
        n = len(as_list(p['eligible_lines']))
        base = rng.randrange(20, 80)
        plans.append({'id': f'SYN-PLAN-{i:02d}', 'title': f'Synthetic Plan {i:02d}', 'eligible_lines': p['eligible_lines'],
                      'line_prices': same_type(p['line_prices'], [base + 15 * k + rng.randrange(0, 5) for k in range(n)])})
    snap = {'collections': {'Phones': {'rows': phones}, 'Plans': {'rows': plans}}}
    snap['snapshot_sha256'] = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    return snap


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    snap = synthetic_snapshot()
    (OUT / 'synthetic_catalog.json').write_text(json.dumps(snap, indent=1))
    src = (HERE / 'generate.py').read_text()
    for a, b in [("OUT=ROOT/'docs/research-papers/telco-ecra-operational/data'", f"OUT=Path({str(OUT)!r})"),
                 ("SOURCE=ROOT/'docs/research-papers/telco-ecra/data/live_snapshot.json'",
                  f"SOURCE=Path({str(OUT / 'synthetic_catalog.json')!r})"),
                 ("random.Random(20261004)", f"random.Random({SEED})"), ("'seed':20261004", f"'seed':{SEED}"),
                 ("'telco-ecra-operational-v1'", "'telco-ecra-public-v1'"),
                 ("'phone SKU and plan-array fields copied from the frozen 2026-10-03 ISP capture'",
                  "'synthetic stand-ins for phone and plan fields; no real catalog data'"),
                 ("origin='real ISP catalog fields copied into a synthetic scenario'", "origin='synthetic catalog stand-in'"),
                 ("origin='real ISP catalog fields; plan_price output uses untyped source numeric units'",
                  "origin='synthetic catalog stand-in'")]:
        assert src.count(a) >= 1, a
        src = src.replace(a, b)
    ns = {'__name__': 'generate_public_impl', '__file__': str(HERE / 'generate.py')}
    exec(compile(src, 'generate.py (public variant)', 'exec'), ns)
    ns['generate']()


if __name__ == '__main__':
    main()
