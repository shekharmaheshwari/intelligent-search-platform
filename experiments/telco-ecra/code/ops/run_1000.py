"""TELCO-ECRA paper v17: every approach of the paper re-run, once, on the 1,000-case benchmark (generate_1000.py).

Frozen before the sets were generated, and hashed in data/preregistration.json before the first request:
  * round-1 rules/prompt (A1, A2) and round-2 rules/prompt (A4, A5), the four worked examples (A5)
  * the six programs GPT-5.4-mini wrote earlier (round 1: v2_code_run1-3.py, round 2: v3_code_run1-3.py); they are
    not regenerated, so no program has seen any of the 1,000 cases
  * program selection: most exact cases on the 200 practice cases, ties to the lowest run
Arms on the 800 test cases:
  A1 GPT-4o-mini direct, round-1 prompt, default settings (max_tokens 1024), 1 run
  A2 GPT-5.4-mini direct, round-1 prompt, low effort, 3 runs (seeds 1-3)
  A3 round-1 programs (3)            A4 GPT-5.4-mini direct, round-2 prompt, low effort, 3 runs
  A5 GPT-5.4-mini direct, round-2 prompt, medium effort + 4 examples, 1 run
  A6 round-2 programs (3)            hand-written checker, and the checker with each group of checks removed
Usage: python run_1000.py [run|score]   (no argument = pre-register, run, score)"""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OPS = ROOT / 'experiments/telco_ecra_operational'
sys.path.insert(0, str(OPS))
import run_baselines_v3 as r3  # noqa: E402  frozen round-2 prompt, examples, call wrapper
import run_baselines_v2 as v2  # noqa: E402  (r3 has pointed v2.DATA at the v3 data folder)
import runtime  # noqa: E402  frozen round-1 prompt
from verifier import verify  # noqa: E402
from score_model import same, FIELDS  # noqa: E402

DATA = HERE / 'data'
RUNS = DATA / 'runs'
FROZEN = ROOT / 'docs/research-papers/telco-ecra-operational-v3/data'
PROGRAMS = {'R1': [FROZEN / f'v2_code_run{i}.py' for i in (1, 2, 3)], 'R2': [FROZEN / f'v3_code_run{i}.py' for i in (1, 2, 3)]}
R1_SYSTEM = runtime.SYSTEM
r3.LOG = RUNS / 'attempts.jsonl'
ARMS = [('A1_4o_r1', 1, None), ('A2_r1_low', 1, 'low'), ('A2_r1_low', 2, 'low'), ('A2_r1_low', 3, 'low'),
        ('A4_r2_low', 1, 'low'), ('A4_r2_low', 2, 'low'), ('A4_r2_low', 3, 'low'), ('A5_r2_medium_ex', 1, 'medium')]
ABLATIONS = [None, 'no_temporal', 'no_identity', 'no_currency']


def sha(x):
    return hashlib.sha256(x if isinstance(x, bytes) else x.encode()).hexdigest()


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


def preregister():
    f = DATA / 'preregistration.json'
    assert not f.exists(), 'pre-registration exists; this benchmark is run once'
    m = {'written_before_first_call': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
         'sets': {n: {'sha256': sha((DATA / n / 'benchmark.json').read_bytes()), 'seed': json.loads((DATA / n / 'benchmark.json').read_text())['seed'],
                      'cases': len(json.loads((DATA / n / 'benchmark.json').read_text())['cases'])} for n in ['practice', 'test']},
         'arms': [{'arm': a, 'run': r, 'effort': e} for a, r, e in ARMS],
         'models': {'A1': 'gpt-4o-mini', 'others': r3.MODEL},
         'program_selection': 'most exact cases on the 200 practice cases; ties to the lowest run',
         'analysis': 'exact cases, unsupported answers (value where cannot-tell expected), wrongly withheld answers, '
                     'per-answer outcomes (Table I), checker ablation (Table II), all approaches (Table III, Fig. 2); run once, no changes after',
         'sha256': {'r1_system': sha(R1_SYSTEM), 'r2_system': sha(r3.SYSTEM_DIRECT), 'examples': sha(r3.examples_text()),
                    **{p.name: sha(p.read_bytes()) for ps in PROGRAMS.values() for p in ps},
                    'run_1000.py': sha(Path(__file__).read_bytes()), 'generate_1000.py': sha((HERE / 'generate_1000.py').read_bytes()),
                    'generate.py': sha((OPS / 'generate.py').read_bytes()), 'verifier.py': sha((OPS / 'verifier.py').read_bytes())}}
    v3 = json.loads((FROZEN / 'v3_preregistration.json').read_text())['sha256']
    assert m['sha256']['r2_system'] == v3['system_direct'] and m['sha256']['examples'] == v3['examples'], 'round-2 prompt drifted'
    f.write_text(json.dumps(m, indent=2)); print('pre-registered')


async def call_4o(client, user, sem, tag):
    async with sem:
        for attempt in range(6):
            t = time.time()
            try:
                r = await client.chat.completions.create(model='gpt-4o-mini', max_tokens=1024,
                                                         messages=[{'role': 'system', 'content': R1_SYSTEM}, {'role': 'user', 'content': user}])
                u = r.usage
                with r3.LOG.open('a') as f:
                    f.write(json.dumps({'tag': tag, 'attempt': attempt + 1, 'status': 'ok', 'time': time.time()}) + '\n')
                return {'text': r.choices[0].message.content, 'model': r.model, 'seconds': time.time() - t,
                        'usage': {'in': u.prompt_tokens, 'out': u.completion_tokens, 'reasoning': 0}, 'attempts': attempt + 1}
            except Exception as e:
                with r3.LOG.open('a') as f:
                    f.write(json.dumps({'tag': tag, 'attempt': attempt + 1, 'status': 'error', 'error': repr(e)[:200], 'time': time.time()}) + '\n')
                if attempt == 5:
                    return {'text': None, 'error': repr(e)[:300], 'attempts': attempt + 1}
                await asyncio.sleep(2 ** attempt + 1)


def parse_strict(raw):
    try:
        return runtime.parse(raw or '')
    except Exception:
        return None


def done(name):
    f = RUNS / f'{name}.jsonl'
    return {j['case_id']: j for j in map(json.loads, f.read_text().splitlines())} if f.exists() else {}


async def run():
    from openai import AsyncOpenAI
    test, _ = load('test'); assert len(test) == 800
    client = AsyncOpenAI(max_retries=0); sem = asyncio.Semaphore(12); ex = r3.examples_text()
    for arm, run_, effort in ARMS:
        name = f'{arm}_run{run_}'
        have = done(name)
        todo = [c for c in test if c['case_id'] not in have or have[c['case_id']].get('text') is None]
        if not todo:
            continue
        if arm == 'A1_4o_r1':
            coros = [call_4o(client, json.dumps(c['input'], sort_keys=True), sem, f'{name}:{c["case_id"]}') for c in todo]
        elif arm == 'A2_r1_low':
            coros = [r3.call(client, R1_SYSTEM, json.dumps(c['input']), run_, True, effort, sem, f'{name}:{c["case_id"]}') for c in todo]
        else:
            pre = ('Worked examples (input and expected output):\n' + ex + '\n\nCase:\n') if arm == 'A5_r2_medium_ex' else ''
            coros = [r3.call(client, r3.SYSTEM_DIRECT, pre + json.dumps(c['input']), run_, True, effort, sem, f'{name}:{c["case_id"]}') for c in todo]
        res = await asyncio.gather(*coros)
        with (RUNS / f'{name}.jsonl').open('a') as f:
            for c, r in zip(todo, res):
                ans = parse_strict(r.get('text')) if arm == 'A1_4o_r1' else v2.parse(r.get('text'))
                f.write(json.dumps({'case_id': c['case_id'], 'answer': ans, **r}) + '\n')
        print(name, 'calls', len(todo), 'failed', sum(r.get('text') is None for r in res), flush=True)


def outcomes(pred, oracle, ids):
    o = {'exact': 0, 'unsupported': 0, 'withheld_wrongly': 0, 'invalid': 0,
         'per_answer': {k: {'correct': 0, 'wrong_value': 0, 'unsupported': 0, 'withheld_wrongly': 0} for k in FIELDS}}
    for cid in ids:
        p, t = pred.get(cid), oracle[cid]['expected']
        if p is None:
            o['invalid'] += 1
        ok = p is not None and all(same(p[k], t[k], k) for k in FIELDS)
        o['exact'] += ok
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
    res, err = v2.run_generated(path.read_text(), [{'case_id': c['case_id'], 'input': c['input']} for c in cases])
    return {c['case_id']: (None if err or 'error' in (res or {}).get(c['case_id'], {'error': 1})
                           else {k: res[c['case_id']].get(k) for k in FIELDS}) for c in cases}, err


def score():
    test, oracle_t = load('test'); prac, oracle_p = load('practice')
    ids = [c['case_id'] for c in test]
    out = {'n_test': len(ids), 'n_practice': len(prac), 'arms': {}}
    for arm, run_, _ in ARMS:
        pred = {cid: j['answer'] for cid, j in done(f'{arm}_run{run_}').items()}
        out['arms'].setdefault(arm, []).append(outcomes(pred, oracle_t, ids))
    for rnd, paths in PROGRAMS.items():
        rows = []
        for i, p in enumerate(paths, 1):
            pp, perr = run_program(p, prac); tp, terr = run_program(p, test)
            rows.append({'run': i, 'practice_exact': outcomes(pp, oracle_p, [c['case_id'] for c in prac])['exact'],
                         'practice_error': perr, 'test_error': terr, **outcomes(tp, oracle_t, ids)})
        best = max(rows, key=lambda r: (r['practice_exact'], -r['run']))
        out['arms'][f'programs_{rnd}'] = rows
        out[f'selected_{rnd}'] = best['run']
    for ab in ABLATIONS:
        pred = {c['case_id']: verify(c['case'], c['input']['evidence'], ab)['answer'] if ab else verify(c['case'], c['input']['evidence'])['answer'] for c in test}
        out['arms'][f'checker_{ab or "full"}'] = [outcomes(pred, oracle_t, ids)]
    pr = {c['case_id']: verify(c['case'], c['input']['evidence'])['answer'] for c in prac}
    out['checker_practice_exact'] = outcomes(pr, oracle_p, list(pr))['exact']
    usage = {}
    for f in RUNS.glob('A*.jsonl'):
        u = usage.setdefault(f.stem.split('_run')[0], {'in': 0, 'out': 0, 'calls': 0})
        for j in map(json.loads, f.read_text().splitlines()):
            u['in'] += (j.get('usage') or {}).get('in', 0); u['out'] += (j.get('usage') or {}).get('out', 0); u['calls'] += 1
    out['tokens'] = usage
    (DATA / 'summary_1000.json').write_text(json.dumps(out, indent=2))
    for k, v in out['arms'].items():
        print(f"{k:22s} exact {[r['exact'] for r in v]}  unsup {[r['unsupported'] for r in v]}  withheld {[r['withheld_wrongly'] for r in v]}  invalid {[r['invalid'] for r in v]}"
              + (f"  practice {[r['practice_exact'] for r in v]}" if 'practice_exact' in v[0] else ''))
    print('selected', out['selected_R1'], out['selected_R2'], 'checker practice', out['checker_practice_exact'])


if __name__ == '__main__':
    RUNS.mkdir(parents=True, exist_ok=True)
    a = sys.argv[1:]
    if not a:
        preregister()
    if not a or a == ['run']:
        asyncio.run(run())
    score()
