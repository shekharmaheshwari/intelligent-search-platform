"""Narrow operational verifier; no oracle, family labels, or model access."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json

def time(value):
    try:
        t=datetime.fromisoformat(value.replace('Z','+00:00'))
        return t if t.tzinfo else None
    except (AttributeError,ValueError,TypeError):return None

def amount(value):
    if isinstance(value,bool):return None
    try:
        d=Decimal(str(value));return d if d.is_finite() else None
    except (InvalidOperation,TypeError,ValueError):return None

def one(rows,kind):
    found=[r for r in rows if r['kind']==kind]
    return found[0] if len(found)==1 else None

def promotion(rows,ctx,*,temporal=True,identity=True):
    t=time(ctx.get('activation_time'));known=time(ctx.get('knowledge_time'))
    if t is None or known is None:return None,'missing_activation_or_knowledge_time'
    offers=[r for r in rows if r['kind']=='promotion']
    if any(time(r.get('recorded_at')) is None for r in offers):return None,'invalid_offer_recording_time'
    offers=[r for r in offers if not temporal or time(r['recorded_at'])<=known]
    if not offers:return None,'no_visible_offer'
    if len({r.get('offer_id') for r in offers})!=1:return None,'multiple_offer_identities'
    if any(type(r.get('version')) is not int for r in offers):return None,'invalid_version'
    version=max(r['version'] for r in offers);latest=[r for r in offers if r['version']==version]
    payload=lambda r:json.dumps({k:v for k,v in r.items() if k not in ['id']},sort_keys=True)
    if len({payload(r) for r in latest})!=1:return None,'conflicting_same_version_offer'
    p=latest[0];lo=time(p.get('valid_from'));hi=time(p.get('valid_to'))
    if lo is None or hi is None or lo>=hi:return None,'invalid_offer_interval'
    rules=['eligible_skus','eligible_plans','excluded_segments']
    if any(not isinstance(p.get(k),list) for k in rules):return None,'missing_mandatory_rule'
    if any(ctx.get(k) is None for k in ['sku','plan_id','segment']):return None,'missing_account_identity'
    checks=[ctx['segment'] not in p['excluded_segments']]
    if identity:checks.extend([ctx['sku'] in p['eligible_skus'],ctx['plan_id'] in p['eligible_plans']])
    if temporal:checks.append(lo<=t<hi)
    return all(checks),'mandatory_eligibility_checks'

def billing(rows,ctx,*,temporal=True,currency=True):
    event=time(ctx.get('event_time'));known=time(ctx.get('knowledge_time'));complete=time(ctx.get('ledger_complete_through'))
    if event is None or known is None or complete is None or complete<known:return None,'incomplete_ledger'
    entries=[r for r in rows if r['kind']=='billing' and r.get('service_period')==ctx.get('service_period')]
    if any(time(r.get('event_time')) is None or time(r.get('posted_at')) is None for r in entries):return None,'invalid_event_time'
    visible=[r for r in entries if not temporal or (time(r['event_time'])<=event and time(r['posted_at'])<=known)]
    by_id={}
    for r in visible:
        eid=r.get('event_id')
        if not eid:return None,'missing_business_event_id'
        signature=tuple(str(r.get(k)) for k in ['amount','currency','event_time','posted_at','service_period'])
        if eid in by_id and by_id[eid][0]!=signature:return None,'conflicting_duplicate_event'
        by_id[eid]=(signature,r)
    total=Decimal(0)
    for _,r in by_id.values():
        value=amount(r.get('amount'))
        if value is None:return None,'invalid_amount'
        if currency and (not ctx.get('currency') or r.get('currency')!=ctx['currency']):return None,'missing_or_mixed_currency'
        total+=value
    return str(total),'posted_event_sum_with_deduplication'

def plan(rows,ctx):
    matches=[r for r in rows if r['kind']=='catalog_link' and r.get('catalog_kind')=='plan' and r.get('catalog_id')==ctx.get('plan_id')]
    if len(matches)!=1:return None,'plan_identity'
    p=matches[0];lines=p.get('eligible_lines');prices=p.get('line_prices');n=ctx.get('lines')
    if not isinstance(lines,list) or not isinstance(prices,list) or len(lines)!=len(prices) or not lines:return None,'malformed_price_table'
    if any(type(k) is not int or k<1 for k in lines) or len(set(lines))!=len(lines) or type(n) is not int:return None,'invalid_line_domain'
    if n not in lines:return None,'unlisted_configuration'
    value=amount(prices[lines.index(n)])
    return (str(value),'exact_source_table_lookup') if value is not None and value>=0 else (None,'invalid_price')

def verify(case,records,ablation=None):
    rows=[r for r in records if r.get('case_id')==case['case_id'] and r.get('account_id')==case['account_id']]
    ctx=one(rows,'account');names=['promotion_eligible','net_billed','support_correct','plan_price']
    if ctx is None:return {'answer':dict.fromkeys(names),'reasons':{'context':'missing_or_ambiguous'},'evidence_ids':[r['id'] for r in rows]}
    # Query timestamps are trusted experiment inputs, not an agent-note override.
    ctx={**ctx,'event_time':case['event_time'],'knowledge_time':case['knowledge_time']}
    eligible,er=promotion(rows,ctx,temporal=ablation!='no_temporal',identity=ablation!='no_identity')
    net,nr=billing(rows,ctx,temporal=ablation!='no_temporal',currency=ablation!='no_currency')
    price,pr=plan(rows,ctx);support=one(rows,'support');correct=None
    if net is not None and support and time(support.get('recorded_at')) and time(support['recorded_at'])<=time(case['knowledge_time']):
        if support.get('currency')==ctx.get('currency') and amount(support.get('asserted_net')) is not None:
            correct=amount(support['asserted_net'])==amount(net)
    answer=dict(zip(names,[eligible,net,correct,price]))
    receipt={'answer':answer,'reasons':dict(zip(names,[er,nr,'compare_agent_assertion_to_verified_ledger',pr])),
             'evidence_ids':sorted(r['id'] for r in rows),'ablation':ablation}
    receipt['evidence_sha256']=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    return receipt

def render(answer):
    e=answer['promotion_eligible'];net=answer['net_billed'];sc=answer['support_correct'];price=answer['plan_price']
    return ('Promotion: '+('unresolved' if e is None else 'eligible' if e else 'not eligible')+
            '. Net billed: '+('unresolved' if net is None else net+' USD (synthetic ledger)')+
            '. Support numeric assertion: '+('unresolved' if sc is None else 'consistent' if sc else 'inconsistent')+
            '. Plan table value: '+('unavailable' if price is None else price+' source units')+'.')
