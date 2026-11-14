"""Send data/prompts.jsonl to the generator. Same model, seed, token limit and message layout as
../ztvi_measured_500/generate_rounds2_5.py. Resumable: progress in data/generations.jsonl.partial.
Refuses to overwrite a completed data/generations.jsonl.
"""
import hashlib, json, os, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from openai import OpenAI

HERE = Path(__file__).resolve().parent
D = HERE / 'data'
OUT = D / 'generations.jsonl'
PARTIAL = OUT.with_suffix('.jsonl.partial')
WORKERS, ATTEMPTS = 4, 6


def key():
    if os.environ.get('OPENAI_API_KEY'):
        return os.environ['OPENAI_API_KEY']
    for line in (HERE.parents[1] / 'backend/.env').read_text().splitlines():
        if line.startswith('OPENAI_API_KEY='):
            return line.split('=', 1)[1].strip().strip('"\'')
    raise SystemExit('no key')


def main():
    if OUT.exists():
        raise SystemExit('completed run present; preserve it before re-running')
    design = json.loads((D / 'design.json').read_text())
    assert hashlib.sha256((D / 'prompts.jsonl').read_bytes()).hexdigest() == design['prompts_sha256'], 'prompts changed after design was frozen'
    prompts = [json.loads(l) for l in open(D / 'prompts.jsonl')]
    done = {json.loads(l)['id'] for l in open(PARTIAL)} if PARTIAL.exists() else set()
    todo = [p for p in prompts if p['id'] not in done]
    print('to send', len(todo), 'already done', len(done), flush=True)
    client = OpenAI(api_key=key())
    lock, out, count = threading.Lock(), open(PARTIAL, 'a'), [len(done)]

    def work(p):
        row = {'id': p['id'], 'model': design['model']}
        if not p['passages']:
            row.update(answer='', sent=False)
        else:
            docs = '\n'.join(f'[{i}] {x}' for i, x in enumerate(p['passages'], 1))
            for attempt in range(ATTEMPTS):
                try:
                    r = client.chat.completions.create(model=design['model'], seed=design['seed'],
                                                       max_completion_tokens=design['max_completion_tokens'], messages=[
                        {'role': 'system', 'content': p['system']},
                        {'role': 'user', 'content': f'Documents:\n{docs}\n\nQuestion: {design["question"]}'}])
                    break
                except Exception as e:
                    if attempt == ATTEMPTS - 1:
                        raise
                    print('retry', type(e).__name__, flush=True)
                    time.sleep(2 ** attempt)
            row.update(answer=r.choices[0].message.content or '', sent=True, response_model=r.model,
                       finish=r.choices[0].finish_reason, usage=[r.usage.prompt_tokens, r.usage.completion_tokens])
        with lock:
            out.write(json.dumps(row) + '\n'); out.flush()
            count[0] += 1
            if count[0] % 200 == 0:
                print(count[0], flush=True)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(work, todo))
    out.close()
    rows = [json.loads(l) for l in open(PARTIAL)]
    assert len(rows) == len(prompts) and len({r['id'] for r in rows}) == len(prompts)
    os.replace(PARTIAL, OUT)
    print('complete', len(rows))


if __name__ == '__main__':
    main()
