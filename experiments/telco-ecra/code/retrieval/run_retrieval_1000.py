"""TELCO-ECRA paper v17, retrieval study on the 1,000-case world (generate_world.py here: seed 20261013, 1,000 target
cases of which 800 are scored, 10,000 distractor accounts, invented catalogue). Every query form, policy and rule below
was fixed on the earlier 200-case world before this world was generated, and is applied unchanged and run once:
  run_pipeline     GPT-5.4-mini messages, parse, offer-only search (top-1 / model picks), direct answers
  posthoc_query    phone + offer search, model picks, direct answers
  claude_questions Claude Sonnet 5.5 messages, same pipeline (offer only and phone + offer)
  hybrid_search    keyword + voyage-3 embeddings (rank fusion, alpha 0.3)
  ambiguity        message classes (identifiable / not), re-scoring
  clarify_policy   phone-filtered search, ask when the top hits tie
Writes data/runs/preregistration_1000.json before the first request."""
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SCRIPTS = ['run_pipeline.py', 'posthoc_query.py', 'claude_questions.py', 'hybrid_search.py', 'ambiguity.py', 'clarify_policy.py',
           'retrieval.py', 'generate_world.py', 'run_retrieval_1000.py']


def main():
    import run_pipeline as P
    P.RUNS.mkdir(parents=True, exist_ok=True)
    f = P.RUNS / 'preregistration_1000.json'
    if not f.exists():
        f.write_text(json.dumps({'written_before_first_call': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                                 'world_sha256': json.loads((HERE / 'data/design.json').read_text())['world_sha256'],
                                 'scored_cases': 'the 800 test cases', 'status': 'all arms fixed before this world existed; run once',
                                 'program': str(P.PROGRAM.name), 'program_sha256': P.sha(P.PROGRAM.read_bytes()),
                                 'sha256': {s: hashlib.sha256((HERE / s).read_bytes()).hexdigest() for s in SCRIPTS},
                                 'prompts': {'question': P.sha(P.QUESTION_SYS), 'parse': P.sha(P.PARSE_SYS), 'select': P.sha(P.SELECT_SYS),
                                             'direct': P.sha(P.r3.SYSTEM_DIRECT)}}, indent=2))
        P.preregister(P.load()[0])
    P.main(['questions', 'parse', 'select', 'direct', 'score'])
    import posthoc_query; posthoc_query.main()
    import claude_questions; claude_questions.main()
    import hybrid_search as H; H.build(); H.run()
    import ambiguity; ambiguity.main()
    import clarify_policy; clarify_policy.main()


if __name__ == '__main__':
    main()
