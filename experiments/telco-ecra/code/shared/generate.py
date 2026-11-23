"""Versioned synthetic operational fixtures linked to a frozen, real ISP catalog.

The oracle is assigned by scenario construction and never calls the verifier.
No record represents a real customer, invoice, offer, or support interaction.
"""
import hashlib
import json
import random
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/research-papers/telco-ecra-operational/data'
SOURCE=ROOT/'docs/research-papers/telco-ecra/data/live_snapshot.json'
FAMILIES=['clean','sku_mismatch','plan_mismatch','excluded_segment','expired',
          'not_started','end_boundary','late_credit','future_event','wrong_currency',
          'missing_currency','conflicting_offer','missing_offer','misleading_support',
          'correct_support','duplicate_event','conflicting_event','unknown_activation',
          'unlisted_line_count','future_offer_version']
FIELDS=['promotion_eligible','net_billed','support_correct','plan_price']

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def generate():
    OUT.mkdir(parents=True,exist_ok=True)
    snapshot=json.loads(SOURCE.read_text());phones=snapshot['collections']['Phones']['rows'];plans=snapshot['collections']['Plans']['rows']
    rng=random.Random(20261004);records=[];cases=[];labels=[]
    for family_index,family in enumerate(FAMILIES):
        for replicate in range(10):
            ordinal=family_index*10+replicate;case=f'syn-{ordinal:03d}';account=f'SYNTHETIC-ACCOUNT-{ordinal:03d}'
            split='development' if replicate<2 else 'test'
            phone=phones[rng.randrange(len(phones))];plan=plans[rng.randrange(len(plans))]
            lines=rng.choice(plan['eligible_lines']);amount=rng.randrange(70,201);credit=rng.randrange(10,41)
            event_time='2026-06-15T12:00:00+00:00';known_time='2026-06-20T12:00:00+00:00'
            evidence=[];next_id=0
            def add(kind,**payload):
                nonlocal next_id
                r={'id':f'{case}-{next_id:02d}','case_id':case,'account_id':account,'kind':kind,
                   'synthetic':True,'benchmark':'telco-ecra-operational-v1',**payload}
                next_id+=1
                evidence.append(r);return r
            add('catalog_link',catalog_kind='phone',catalog_id=phone['id'],title=phone['title'],skus=phone['skus'],
                origin='real ISP catalog fields copied into a synthetic scenario')
            add('catalog_link',catalog_kind='plan',catalog_id=plan['id'],title=plan['title'],
                eligible_lines=plan['eligible_lines'],line_prices=plan['line_prices'],
                origin='real ISP catalog fields; plan_price output uses untyped source numeric units')
            context=add('account',sku=phone['skus'][0],plan_id=plan['id'],segment='standard',lines=lines,
                activation_time='2026-06-10T00:00:00+00:00',currency='USD',event_time=event_time,knowledge_time=known_time,
                service_period='2026-06',ledger_complete_through=known_time)
            offer=add('promotion',offer_id=f'OFFER-{ordinal}',version=1,recorded_at='2026-05-01T00:00:00+00:00',
                valid_from='2026-06-01T00:00:00+00:00',valid_to='2026-07-01T00:00:00+00:00',
                eligible_skus=[phone['skus'][0]],eligible_plans=[plan['id']],excluded_segments=['employee'],
                credit_amount=str(credit),currency='USD')
            charge=add('billing',event_id=f'CHARGE-{ordinal}',amount=str(amount),currency='USD',
                event_time='2026-06-12T00:00:00+00:00',posted_at='2026-06-13T00:00:00+00:00',service_period='2026-06')
            adjustment=add('billing',event_id=f'CREDIT-{ordinal}',amount=str(-credit),currency='USD',
                event_time='2026-06-14T00:00:00+00:00',posted_at='2026-06-15T00:00:00+00:00',service_period='2026-06')
            # The promotion decision does not determine whether a ledger credit happened.
            expected={'promotion_eligible':True,'net_billed':str(amount-credit),'support_correct':True,
                      'plan_price':str(plan['line_prices'][plan['eligible_lines'].index(lines)])}
            if family=='sku_mismatch':offer['eligible_skus']=['SYNTHETIC-OTHER-SKU'];expected['promotion_eligible']=False
            elif family=='plan_mismatch':offer['eligible_plans']=['SYNTHETIC-OTHER-PLAN'];expected['promotion_eligible']=False
            elif family=='excluded_segment':context['segment']='employee';expected['promotion_eligible']=False
            elif family=='expired':offer['valid_to']='2026-06-09T00:00:00+00:00';expected['promotion_eligible']=False
            elif family=='not_started':offer['valid_from']='2026-06-11T00:00:00+00:00';expected['promotion_eligible']=False
            elif family=='end_boundary':context['activation_time']=offer['valid_to'];expected['promotion_eligible']=False
            elif family=='late_credit':adjustment['posted_at']='2026-06-21T00:00:00+00:00';expected['net_billed']=str(amount)
            elif family=='future_event':adjustment['event_time']='2026-06-16T00:00:00+00:00';expected['net_billed']=str(amount)
            elif family=='wrong_currency':adjustment['currency']='EUR';expected['net_billed']=None
            elif family=='missing_currency':adjustment.pop('currency');expected['net_billed']=None
            elif family=='conflicting_offer':
                other={k:v for k,v in offer.items() if k not in ['id','kind','case_id','account_id','synthetic','benchmark']}
                other['eligible_skus']=['SYNTHETIC-OTHER-SKU'];add('promotion',**other);expected['promotion_eligible']=None
            elif family=='missing_offer':evidence.remove(offer);expected['promotion_eligible']=None
            elif family=='duplicate_event':
                other={k:v for k,v in charge.items() if k not in ['id','kind','case_id','account_id','synthetic','benchmark']};add('billing',**other)
            elif family=='conflicting_event':
                other={k:v for k,v in charge.items() if k not in ['id','kind','case_id','account_id','synthetic','benchmark']};other['amount']=str(amount+5);add('billing',**other);expected['net_billed']=None
            elif family=='unknown_activation':context.pop('activation_time');expected['promotion_eligible']=None
            elif family=='unlisted_line_count':context['lines']=max(plan['eligible_lines'])+1;expected['plan_price']=None
            elif family=='future_offer_version':
                other={k:v for k,v in offer.items() if k not in ['id','kind','case_id','account_id','synthetic','benchmark']}
                other.update(version=2,recorded_at='2026-06-21T00:00:00+00:00',eligible_skus=['SYNTHETIC-OTHER-SKU']);add('promotion',**other)
            # Support makes a narrow numeric assertion. It cannot override ledger facts.
            assertion=str(amount-credit) if expected['net_billed'] is None else expected['net_billed']
            if family=='misleading_support':assertion=str(amount+credit)
            add('support',statement=f'The net billed amount is {assertion} USD.',asserted_net=assertion,currency='USD',
                recorded_at='2026-06-19T00:00:00+00:00',authority='non_authoritative_agent_note')
            expected['support_correct']=None if expected['net_billed'] is None else assertion==expected['net_billed']
            # Freeze data and order independently of the result fields. No label/family reaches the model.
            assert len({r['id'] for r in evidence})==len(evidence)
            rng.shuffle(evidence);records.extend(evidence)
            cases.append({'case_id':case,'account_id':account,'split':split,'event_time':event_time,'knowledge_time':known_time,
                          'question':'Determine promotion eligibility at activation, net posted billing as of the stated event and knowledge times, whether the support numeric assertion is correct, and the raw plan-table value for the requested line count.',
                          'record_ids':[r['id'] for r in evidence]})
            labels.append({'case_id':case,'family':family,'split':split,'expected':expected})
    data={'version':'telco-ecra-operational-v1','seed':20261004,'records':records,'cases':cases}
    (OUT/'benchmark.json').write_text(json.dumps(data,indent=2));(OUT/'oracle.json').write_text(json.dumps(labels,indent=2))
    manifest={'benchmark_sha256':digest(data),'source_snapshot_sha256':snapshot['snapshot_sha256'],
              'customers':200,'cases':len(cases),'development_cases':40,'test_cases':160,'families':FAMILIES,
              'synthetic_operational_records':True,'real_customer_records':0,
              'oracle':'assigned by scenario construction; no independent human annotation',
              'split':'two development and eight held-out accounts per family; families and catalog may overlap',
              'real_catalog':'phone SKU and plan-array fields copied from the frozen 2026-10-03 ISP capture',
              'primary_metric':'four-field exact case accuracy on 160 held-out synthetic accounts',
              'secondary_metrics':FIELDS,'collections_prefix':'isp_test_telco_ecra_v1_'}
    (OUT/'design.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'cases':len(cases),'records':len(records),'hash':digest(data)}))

if __name__=='__main__':generate()
