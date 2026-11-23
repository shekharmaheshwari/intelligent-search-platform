"""Recompute paper Tables I-III (1,000-case benchmark) from this archive, with no model or API calls.

Scores every saved model answer (data/ops/runs/*.jsonl) against the oracle, re-executes the six frozen model-written
programs (code/frozen/) on the practice and test sets, runs the hand-written checker with each group of checks removed,
and compares every count with the saved summary (data/ops/summary_1000.json).

Usage: python code/replay.py            (run from experiments/telco-ecra/)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE.parent
DATA = ARCHIVE / 'data/ops'
sys.path.insert(0, str(HERE / 'shared'))
from verifier import verify  # noqa: E402
from score_model import same, FIELDS  # noqa: E402

ARMS = [('A1_4o_r1', 1), ('A2_r1_low', 1), ('A2_r1_low', 2), ('A2_r1_low', 3),
        ('A4_r2_low', 1), ('A4_r2_low', 2), ('A4_r2_low', 3), ('A5_r2_medium_ex', 1)]
PROGRAMS = {'R1': [HERE / f'frozen/v2_code_run{i}.py' for i in (1, 2, 3)],
            'R2': [HERE / f'frozen/v3_code_run{i}.py' for i in (1, 2, 3)]}
ABLATIONS = [None, 'no_temporal', 'no_identity', 'no_currency']
# Same harness as code/shared/run_baselines_v2.py: each program runs in a separate isolated interpreter.
HARNESS = r'''
import json, sys
src = open(sys.argv[1]).read()
ns = {}
exec(compile(src, "generated", "exec"), ns)
out = {}
for line in open(sys.argv[2]):
    c = json.loads(line)
    try:
        r = ns["solve"](c["input"]["request"], c["input"]["evidence"])
        out[c["case_id"]] = {k: (str(v) if k in ("net_billed", "plan_price") and v is not None and not isinstance(v, str) else v)
                             for k, v in dict(r).items()}
    except Exception as e:
        out[c["case_id"]] = {"error": repr(e)[:200]}
print(json.dumps(out))
'''


def load(name):
    bench = json.loads((DATA / name / 'benchmark.json').read_text())
    oracle = {o['case_id']: o for o in json.loads((DATA / name / 'oracle.json').read_text())}
    recs = {}
    for r in bench['records']:
        recs.setdefault(r['case_id'], []).append(r)
    out = []
    for c in bench['cases']:
        req = {k: v for k, v in c.items() if k not in ('split', 'record_ids')}
        lookup = {r['id']: r for r in recs[c['case_id']]}
        out.append({'case_id': c['case_id'], 'case': c, 'input': {'request': req, 'evidence': [lookup[i] for i in c['record_ids']]}})
    return out, oracle


def outcomes(pred, oracle, ids):
    o = {'exact': 0, 'unsupported': 0, 'withheld_wrongly': 0, 'invalid': 0,
         'per_answer': {k: {'correct': 0, 'wrong_value': 0, 'unsupported': 0, 'withheld_wrongly': 0} for k in FIELDS}}
    for cid in ids:
        p, t = pred.get(cid), oracle[cid]['expected']
        if p is None:
            o['invalid'] += 1
        o['exact'] += p is not None and all(same(p[k], t[k], k) for k in FIELDS)
        for k in FIELDS:
            pa = o['per_answer'][k]
            if p is not None and same(p[k], t[k], k):
                pa['correct'] += 1
            elif p is not None and p[k] is not None and t[k] is None:
                pa['unsupported'] += 1; o['unsupported'] += 1
            elif p is not None and p[k] is None and t[k] is not None:
                pa['withheld_wrongly'] += 1; o['withheld_wrongly'] += 1
            else:
                pa['wrong_value'] += 1
    return o


def run_program(path, cases):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / 'gen.py').write_text(path.read_text()); (d / 'harness.py').write_text(HARNESS)
        (d / 'cases.jsonl').write_text('\n'.join(json.dumps({'case_id': c['case_id'], 'input': c['input']}) for c in cases))
        p = subprocess.run([sys.executable, '-I', str(d / 'harness.py'), str(d / 'gen.py'), str(d / 'cases.jsonl')],
                           cwd=d, capture_output=True, text=True, timeout=300, env={})
        res, err = (None, p.stderr[-500:]) if p.returncode else (json.loads(p.stdout), None)
    return {c['case_id']: (None if err or 'error' in (res or {}).get(c['case_id'], {'error': 1})
                           else {k: res[c['case_id']].get(k) for k in FIELDS}) for c in cases}


def main():
    test, oracle_t = load('test'); prac, oracle_p = load('practice')
    ids = [c['case_id'] for c in test]
    arms = {}
    for arm, run in ARMS:
        rows = [json.loads(x) for x in (DATA / f'runs/{arm}_run{run}.jsonl').read_text().splitlines()]
        arms.setdefault(arm, []).append(outcomes({j['case_id']: j['answer'] for j in rows}, oracle_t, ids))
    selected = {}
    for rnd, paths in PROGRAMS.items():
        rows = []
        for i, p in enumerate(paths, 1):
            rows.append({'run': i, 'practice_exact': outcomes(run_program(p, prac), oracle_p, [c['case_id'] for c in prac])['exact'],
                         **outcomes(run_program(p, test), oracle_t, ids)})
        arms[f'programs_{rnd}'] = rows
        selected[rnd] = max(rows, key=lambda r: (r['practice_exact'], -r['run']))['run']
    for ab in ABLATIONS:
        pred = {c['case_id']: (verify(c['case'], c['input']['evidence'], ab) if ab else verify(c['case'], c['input']['evidence']))['answer'] for c in test}
        arms[f'checker_{ab or "full"}'] = [outcomes(pred, oracle_t, ids)]

    saved = json.loads((DATA / 'summary_1000.json').read_text())
    keys = ('exact', 'unsupported', 'withheld_wrongly', 'invalid', 'per_answer')
    mismatches = [k for k in arms if [{x: r[x] for x in keys} for r in arms[k]] != [{x: r[x] for x in keys} for r in saved['arms'][k]]]
    mismatches += [f'selected_{r}' for r in selected if selected[r] != saved[f'selected_{r}']]

    print('Table III (800 test cases): exact / unsupported / wrongly withheld per run')
    for k, v in arms.items():
        print(f"  {k:24s} {[r['exact'] for r in v]}  {[r['unsupported'] for r in v]}  {[r['withheld_wrongly'] for r in v]}"
              + (f"  practice {[r['practice_exact'] for r in v]}" if 'practice_exact' in v[0] else ''))
    print('  selected programs (most practice cases, ties to earliest):', selected)
    print('Table I (A4, mean of three runs): correct / wrong value / unsupported / wrongly withheld')
    for f in FIELDS:
        m = [sum(r['per_answer'][f][x] for r in arms['A4_r2_low']) / 3 for x in ('correct', 'wrong_value', 'unsupported', 'withheld_wrongly')]
        print(f"  {f:20s} " + '  '.join(f'{x:.1f}' for x in m))
    print('Table II (checker ablations): exact / unsupported')
    for ab in ABLATIONS:
        r = arms[f'checker_{ab or "full"}'][0]
        print(f"  {ab or 'all checks':12s} {r['exact']}  {r['unsupported']}")
    print('MATCHES saved summary' if not mismatches else f'MISMATCH: {mismatches}')
    sys.exit(1 if mismatches else 0)


if __name__ == '__main__':
    main()
