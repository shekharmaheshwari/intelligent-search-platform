"""TELCO-ECRA 1,000-case benchmark (paper v17). The unchanged case generator (telco_ecra_operational/generate.py) is
run twice, with separate seeds, on a synthetic catalogue stand-in (generate_public.synthetic_snapshot; no real
catalogue data): a practice set of 200 cases (10 per scenario category, seed 20261011) and a test set of 800 cases
(40 per category, seed 20261012). The rules, prompts, worked examples and model-written programs were all frozen
(and hashed in preregistration.json) before either set was generated. Writes data/practice/ and data/test/."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = HERE.parents[1] / 'telco_ecra_operational'
sys.path.insert(0, str(OPS))
import generate_public as gp  # noqa: E402

SETS = {'practice': (20261011, 10, 'P', 'development'), 'test': (20261012, 40, 'T', 'test')}


def build(name, seed, reps, prefix, split):
    out = HERE / 'data' / name
    out.mkdir(parents=True, exist_ok=True)
    gp.SEED = seed
    snap = gp.synthetic_snapshot()
    (out / 'synthetic_catalog.json').write_text(json.dumps(snap, indent=1))
    src = (OPS / 'generate.py').read_text()
    for a, b in [("OUT=ROOT/'docs/research-papers/telco-ecra-operational/data'", f"OUT=Path({str(out)!r})"),
                 ("SOURCE=ROOT/'docs/research-papers/telco-ecra/data/live_snapshot.json'", f"SOURCE=Path({str(out / 'synthetic_catalog.json')!r})"),
                 ("random.Random(20261004)", f"random.Random({seed})"), ("'seed':20261004", f"'seed':{seed}"),
                 ("for replicate in range(10):", f"for replicate in range({reps}):"),
                 ("ordinal=family_index*10+replicate;case=f'syn-{ordinal:03d}';account=f'SYNTHETIC-ACCOUNT-{ordinal:03d}'",
                  f"ordinal=family_index*{reps}+replicate;case=f'{prefix}-{{ordinal:03d}}';account=f'SYNTHETIC-ACCOUNT-{prefix}{{ordinal:03d}}'"),
                 ("split='development' if replicate<2 else 'test'", f"split={split!r}"),
                 ("'telco-ecra-operational-v1'", "'telco-ecra-1000-v1'"),
                 ("origin='real ISP catalog fields copied into a synthetic scenario'", "origin='synthetic catalogue stand-in'"),
                 ("origin='real ISP catalog fields; plan_price output uses untyped source numeric units'", "origin='synthetic catalogue stand-in'"),
                 ("f'CHARGE-{ordinal}'", f"f'CHARGE-{prefix}{{ordinal}}'"), ("f'CREDIT-{ordinal}'", f"f'CREDIT-{prefix}{{ordinal}}'"),
                 ("f'OFFER-{ordinal}'", f"f'OFFER-{prefix}{{ordinal}}'")]:
        assert src.count(a) >= 1, a
        src = src.replace(a, b)
    ns = {'__name__': 'generate_1000_impl', '__file__': str(OPS / 'generate.py')}
    exec(compile(src, f'generate.py ({name})', 'exec'), ns)
    ns['generate']()
    d = json.loads((out / 'design.json').read_text())
    d.update(customers=20 * reps, cases=20 * reps, development_cases=20 * reps if split == 'development' else 0,
             test_cases=20 * reps if split == 'test' else 0, split=f'{name} set, its own seed',
             real_catalog='none: synthetic catalogue stand-in', collections_prefix=None,
             primary_metric='four-field exact cases')
    (out / 'design.json').write_text(json.dumps(d, indent=2))


if __name__ == '__main__':
    for name, args in SETS.items():
        build(name, *args)
