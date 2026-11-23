"""Paper v2 baselines on the frozen held-out cases (requires OPENAI_API_KEY; sends synthetic case evidence that
includes copied ISP catalog fields to OpenAI, as approved for this study).

  direct : gpt-5.4-mini answers each case from the same system rules and case evidence as the v1 run (3 runs).
  code   : gpt-5.4-mini writes one Python solve() from the same rules and the four v1 development examples with
           their expected outputs; the generated code is executed locally on all 160 test cases (3 generations).

No prompt, rule, or verifier tuning follows these runs. Raw responses and generated code are saved.
"""
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'docs/research-papers/telco-ecra-operational-v2/data'
MODEL = 'gpt-5.4-mini'
RUNS = [1, 2, 3]
FIELDS = ['promotion_eligible', 'net_billed', 'support_correct', 'plan_price']
CODE_INSTRUCTIONS = (
    "Write a Python function `solve(request: dict, evidence: list[dict]) -> dict` that implements the rules above "
    "exactly and returns a dict with the four keys. Use only the Python standard library (decimal and datetime are "
    "allowed). Return decimal amounts as strings and booleans as true booleans; return None where the rules require "
    "null. Do not hard-code case identifiers or answers. Reply with a single Python code block and nothing else.")


def cases():
    retrieved = [json.loads(x) for x in (DATA / 'retrieved_test.jsonl').read_text().splitlines()]
    out = []
    for r in retrieved:
        req = {k: v for k, v in r['case'].items() if k not in ('split', 'record_ids')}
        out.append({'case_id': req['case_id'], 'input': {'request': req, 'evidence': r['records']}})
    return out


def dev_examples():
    bench = json.loads((DATA / 'benchmark.json').read_text())
    oracle = {o['case_id']: o['expected'] for o in json.loads((DATA / 'oracle.json').read_text())}
    ids = [json.loads(x)['case_id'] for x in (DATA / 'predictions_development.jsonl').read_text().splitlines()]
    by_case = {c['case_id']: c for c in bench['cases']}
    ex = []
    for cid in ids:
        req = {k: v for k, v in by_case[cid].items() if k not in ('split', 'record_ids')}
        recs = [r for r in bench['records'] if r['case_id'] == cid]
        ex.append({'input': {'request': req, 'evidence': recs}, 'expected': oracle[cid]})
    return ex


async def call(client, system, user, seed, json_mode, effort, sem):
    async with sem:
        for attempt in range(6):
            try:
                kw = {'response_format': {'type': 'json_object'}} if json_mode else {}
                t = time.time()
                r = await client.chat.completions.create(
                    model=MODEL, seed=seed, reasoning_effort=effort,
                    messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], **kw)
                u = r.usage
                return {'text': r.choices[0].message.content, 'model': r.model, 'seconds': time.time() - t,
                        'usage': {'in': u.prompt_tokens, 'out': u.completion_tokens,
                                  'reasoning': getattr(u.completion_tokens_details, 'reasoning_tokens', 0) or 0}}
            except Exception as e:
                if attempt == 5:
                    return {'text': None, 'error': repr(e)[:300]}
                await asyncio.sleep(2 ** attempt)


def parse(text):
    try:
        d = json.loads(text)
        return {k: d.get(k) for k in FIELDS} if isinstance(d, dict) and set(FIELDS) <= set(d) else None
    except Exception:
        return None


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


def run_generated(code, test_cases):
    """Execute generated code in a separate isolated interpreter with a time limit."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / 'gen.py').write_text(code); (d / 'harness.py').write_text(HARNESS)
        (d / 'cases.jsonl').write_text('\n'.join(json.dumps(c) for c in test_cases))
        p = subprocess.run([sys.executable, '-I', str(d / 'harness.py'), str(d / 'gen.py'), str(d / 'cases.jsonl')],
                           cwd=d, capture_output=True, text=True, timeout=120, env={})
        if p.returncode != 0:
            return None, p.stderr[-500:]
        return json.loads(p.stdout), None


def extract_code(text):
    if text and '```' in text:
        body = text.split('```')[1]
        return body[len('python'):] if body.startswith('python') else body
    return text or ''


async def main():
    from openai import AsyncOpenAI
    client = AsyncOpenAI(max_retries=0)
    system = json.loads((DATA / 'external_prompt_preview.json').read_text())['system']
    test = cases(); sem = asyncio.Semaphore(8)
    assert len(test) == 160
    meta = {'model_alias': MODEL, 'runs': RUNS, 'started': time.strftime('%Y-%m-%dT%H:%M:%S%z')}

    # Direct arm.
    for run in RUNS:
        res = await asyncio.gather(*[call(client, system, json.dumps(c['input']), run, True, 'low', sem) for c in test])
        with (DATA / f'v2_direct_run{run}.jsonl').open('w') as f:
            for c, r in zip(test, res):
                f.write(json.dumps({'case_id': c['case_id'], 'answer': parse(r.get('text')), **r}) + '\n')
        print('direct run', run, 'errors', sum(r.get('text') is None for r in res), flush=True)

    # Code arm.
    examples = dev_examples()
    user = (CODE_INSTRUCTIONS + '\n\nDevelopment examples (input and expected output):\n' +
            '\n'.join(json.dumps(e) for e in examples))
    for run in RUNS:
        r = await call(client, system, user, run, False, 'medium', sem)
        (DATA / f'v2_code_run{run}.py').write_text(extract_code(r.get('text')))
        meta[f'code_run{run}'] = {k: v for k, v in r.items() if k != 'text'}
        print('code run', run, 'generated; inspect before executing', flush=True)
    meta['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    (DATA / 'v2_run_meta.json').write_text(json.dumps(meta, indent=2))


def execute():
    """Run the saved generated code (after manual inspection) on the 160 test cases."""
    test = cases(); meta = json.loads((DATA / 'v2_run_meta.json').read_text())
    for run in RUNS:
        code = (DATA / f'v2_code_run{run}.py').read_text()
        outputs, err = run_generated(code, test) if code.strip() else (None, 'no code')
        with (DATA / f'v2_code_run{run}.jsonl').open('w') as f:
            for c in test:
                o = (outputs or {}).get(c['case_id'])
                ans = {k: o.get(k) for k in FIELDS} if o and 'error' not in o and set(FIELDS) <= set(o) else None
                f.write(json.dumps({'case_id': c['case_id'], 'answer': ans,
                                    'error': (o or {}).get('error') if o else err}) + '\n')
        meta[f'code_run{run}']['execution_error'] = err
        print('code run', run, 'exec error', err)
    (DATA / 'v2_run_meta.json').write_text(json.dumps(meta, indent=2))


if __name__ == '__main__':
    execute() if sys.argv[1:] == ['execute'] else asyncio.run(main())
