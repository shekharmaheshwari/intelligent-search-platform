"""Post hoc ambiguity analysis (no model calls). For every customer message, decide whether its wording identifies
one offer among the offers that exist for the customer's own phone:

  offer name = <phone> <season> <offer kind><suffix>.  A message is consistent with an offer when it contains the
  offer's season and offer type (with or without the word 'credit'), and, if the message mentions a suffix (Plus,
  Bonus, Reward, Extra or 2026 right after the offer type; online or in-store anywhere), the offer carries it.
  'Deal' is never read as a suffix, since customers use it as an everyday word.

Classes: 'identifies' (exactly the true offer is consistent), 'none_exists' (missing-offer case and no existing offer
fits), 'ambiguous' (several fit), 'misnamed' (no offer or only a different offer fits).
Re-scoring: for 'identifies' and 'none_exists' messages the expected answers are unchanged; for the rest the
eligibility answer the system should give is "cannot tell" (it can't know which offer is meant), the other three
answers are unchanged. Every stored run is re-scored by message class. Writes data/runs/ambiguity_summary.json."""
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_pipeline as P  # noqa: E402
from generate_world import SEASONS, OFFER_KINDS, NAME_SUFFIXES  # noqa: E402

norm = lambda s: ' ' + re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]', ' ', (s or '').lower())).strip() + ' '
SUFFIX_WORDS = {s: norm(s.strip('() ')) for s in NAME_SUFFIXES if s}


def parts(name, phone):
    rest = name[len(phone):].strip()
    season = next(s for s in sorted(SEASONS, key=len, reverse=True) if rest.startswith(s))
    rest = rest[len(season):].strip()
    kind = next(k for k in OFFER_KINDS if rest.startswith(k))
    return season, kind, rest[len(kind):]


def consistent(msg, name, phone):
    season, kind, suffix = parts(name, phone)
    core = norm(kind.replace(' Credit', ''))  # 'trade in', 'switcher', ...: customers often drop the word 'credit'
    if norm(season) not in msg or core not in msg:
        return False
    # A suffix counts as mentioned only right after the offer type (with or without 'credit'), except online/in-store,
    # which count anywhere. 'Deal' is never treated as a suffix: customers use it as an everyday word.
    mentioned = []
    for s, w in SUFFIX_WORDS.items():
        if s.strip() == 'Deal':
            continue
        if 'Online' in s or 'In-Store' in s:
            hit = w in msg
        else:
            hit = (core.rstrip() + w) in msg or (core.rstrip() + ' credit' + w) in msg
        if hit:
            mentioned.append(s)
    return all(m == suffix for m in mentioned) if mentioned else True


def classify(cases, by, questions):
    con = sqlite3.connect(HERE / 'data/telco_ecra.sqlite')
    offers = {}
    for oid, name in con.execute('SELECT DISTINCT offer_id, offer_name FROM offers'):
        offers.setdefault(name, oid)
    out = {}
    for c in cases:
        msg = norm(P.js(questions[c['case_id']].get('text'), 'message'))
        fit = {offers[n] for n in offers if n.startswith(c['phone_title'] + ' ') and
               n[len(c['phone_title']) + 1:].split(' ')[0] in [s.split(' ')[0] for s in SEASONS] and consistent(msg, n, c['phone_title'])}
        truth = P.true_offer(by[c['case_id']])
        if truth is None:
            out[c['case_id']] = 'none_exists' if not fit else ('ambiguous' if len(fit) > 1 else 'misnamed')
        else:
            out[c['case_id']] = 'identifies' if fit == {truth} else ('ambiguous' if len(fit) > 1 and truth in fit else 'misnamed')
    return out


def configs():
    """(question source, label, parse cache, query form, select cache or None, direct cache or None)."""
    return [('gpt', 'offer only, top-1', 'parse', 'offer', None, 'direct_top1'),
            ('gpt', 'offer only, model picks', 'parse', 'offer', 'select', 'direct_select'),
            ('gpt', 'phone + offer, top-1', 'parse', 'both', None, None),
            ('gpt', 'phone + offer, model picks', 'parse', 'both', 'select_fixed', 'direct_select_fixed'),
            ('claude', 'offer only, top-1', 'parse_claude', 'offer', None, None),
            ('claude', 'offer only, model picks', 'parse_claude', 'offer', 'select_claude_offer_only', None),
            ('claude', 'phone + offer, top-1', 'parse_claude', 'both', None, None),
            ('claude', 'phone + offer, model picks', 'parse_claude', 'both', 'select_claude_phone_offer', 'direct_claude_phone_offer_select')]


def query(t, form):
    p = P.js(t) or {}
    if form == 'offer':
        return p.get('offer_mention') or p.get('phone_mention') or ''
    return ' '.join(x for x in [p.get('phone_mention'), p.get('offer_mention')] if x).strip()


def main():
    cases, oracle, by = P.load()
    classes = {'gpt': classify(cases, by, P.cached('questions')), 'claude': classify(cases, by, P.cached('questions_claude'))}
    res = {'classes': {s: {k: list(v.values()).count(k) for k in ['identifies', 'none_exists', 'ambiguous', 'misnamed']} for s, v in classes.items()},
           'runs': []}
    for src, label, pc, form, sc, dc in configs():
        parsed, sel = P.cached(pc), (P.cached(sc) if sc else {})
        recs = {}
        for c in cases:
            q = query(parsed[c['case_id']].get('text'), form)
            hits = P.R.search_offers(q) if q else []
            if sc:
                s = P.js(sel[c['case_id']].get('text'), 'offer_id')
                oid = s if s in {h['offer_id'] for h in hits} else None
            else:
                oid = hits[0]['offer_id'] if hits else None
            recs[c['case_id']] = P.R.assemble(c, c['account_id'], oid)
        answers = {'checker': {c['case_id']: P.verify(c, recs[c['case_id']])['answer'] for c in cases},
                   'program': P.decide_program(cases, recs)}
        if dc:
            answers['direct'] = {cid: P.parse_answer(r.get('text')) for cid, r in P.cached(dc).items()}
        for name, ans in answers.items():
            row = {'questions': src, 'retrieval': label, 'decision': name}
            for group, members in [('answerable', {'identifies', 'none_exists'}), ('not identifiable', {'ambiguous', 'misnamed'})]:
                ex = un = n = 0
                for c in cases:
                    if classes[src][c['case_id']] not in members:
                        continue
                    n += 1
                    t = dict(oracle[c['case_id']]['expected'])
                    if group == 'not identifiable':
                        t['promotion_eligible'] = None
                    a = ans.get(c['case_id'])
                    ex += a is not None and all(P.same(a[k], t[k], k) for k in P.FIELDS)
                    un += 0 if a is None else sum(a[k] is not None and t[k] is None for k in P.FIELDS)
                row[group] = {'n': n, 'exact': ex, 'unsupported': un}
            res['runs'].append(row)
    (P.RUNS / 'ambiguity_summary.json').write_text(json.dumps(res, indent=2))
    print(json.dumps(res['classes']))
    for r in res['runs']:
        a, b = r['answerable'], r['not identifiable']
        print(f"{r['questions']:6s} {r['retrieval']:28s} {r['decision']:8s} answerable {a['exact']:3d}/{a['n']:3d} (unsup {a['unsupported']:2d})   "
              f"not identifiable {b['exact']:3d}/{b['n']:3d} (unsup {b['unsupported']:2d})")


if __name__ == '__main__':
    main()
