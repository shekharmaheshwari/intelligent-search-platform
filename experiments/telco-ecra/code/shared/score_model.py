"""Score saved model outputs against the withheld construction oracle; no API calls."""
import csv
import json
from decimal import Decimal,InvalidOperation
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'docs/research-papers/telco-ecra-operational/data'
FIELDS=['promotion_eligible','net_billed','support_correct','plan_price']

def same(a,b,key):
    if a is None or b is None:return a is None and b is None
    if key in ['promotion_eligible','support_correct']:return type(a) is bool and a==b
    try:return Decimal(str(a)).is_finite() and Decimal(str(a))==Decimal(str(b))
    except (InvalidOperation,ValueError,TypeError):return False

def main():
    labels={r['case_id']:r for r in json.loads((DATA/'oracle.json').read_text()) if r['split']=='test'}
    predictions=[json.loads(x) for x in (DATA/'predictions_test.jsonl').read_text().splitlines()]
    assert len(predictions)==len(labels)==160 and {r['case_id'] for r in predictions}==set(labels)
    rows=[];aggregates={}
    for r in predictions:
        target=labels[r['case_id']]['expected']
        for method,pred in [('model',r['baseline']),('verified_repair',r['verified']['answer'])]:
            valid=pred is not None;pred=pred or dict.fromkeys(FIELDS)
            row={'case_id':r['case_id'],'family':labels[r['case_id']]['family'],'method':method,'valid_output':valid}
            for k in FIELDS:
                row[k+'_correct']=valid and same(pred[k],target[k],k)
                row[k+'_answered']=valid and pred[k] is not None
                row[k+'_unsupported']=valid and pred[k] is not None and target[k] is None
            row['case_exact']=all(row[k+'_correct'] for k in FIELDS);rows.append(row)
    with (DATA/'model_case_metrics.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    for method in ['model','verified_repair']:
        rr=[r for r in rows if r['method']==method];answered=sum(r[k+'_answered'] for r in rr for k in FIELDS)
        aggregates[method]={'cases':len(rr),'case_exact':sum(r['case_exact'] for r in rr),
            'field_correct':sum(r[k+'_correct'] for r in rr for k in FIELDS),'fields':640,
            'answered_fields':answered,'unsupported_fields':sum(r[k+'_unsupported'] for r in rr for k in FIELDS),
            'accuracy_among_answered':sum(r[k+'_answered'] and r[k+'_correct'] for r in rr for k in FIELDS)/answered if answered else None,
            'per_field':{k:sum(r[k+'_correct'] for r in rr) for k in FIELDS}}
    out={'results':aggregates,'errors':sum(r['error_type'] is not None for r in predictions),
         'limitation':'single model run; known synthetic rule families; machine-assigned oracle; structured four-field outputs, not free-prose semantic judgments'}
    (DATA/'model_summary.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))

if __name__=='__main__':main()
