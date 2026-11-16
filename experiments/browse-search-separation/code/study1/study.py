"""Offline browse/search separation study. All customers and records are synthetic.

No ISP service imports beyond the pure model module; no network or database access.
Simulation decisions are fixed in CONFIG before inspecting study results.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/src'))
import numpy as np
from isp.services.domain.popularity_model import fit_scores, position_weight

OUT=ROOT/'docs/research-papers/browse-search-separation'
CONFIG={'customers':[100,200],'seeds':list(range(20)),'products':120,'categories':6,
        'train_customer_fraction':0.8,'train_sessions':20,'checkpoints':[5,20],
        'evaluation_sessions':5,'slate_size':10,'evaluation_candidates':30,
        'examination_exponent':1.1,'click_noise':0.03,
        'scenarios':{
            'aligned':{'alignment':1.0,'search_share':0.5,'future_drift':0.0},
            'shifted':{'alignment':0.0,'search_share':0.5,'future_drift':0.0},
            'sparse_browse':{'alignment':0.0,'search_share':0.9,'future_drift':0.0},
            'preference_drift':{'alignment':0.0,'search_share':0.5,'future_drift':1.0},
        },
        'methods':['static','formula_mixed','formula_browse','mixed','mixed_matched','browse'],
        'primary_comparison':'browse minus mixed_matched; unseen customers; session 20',
        'event_encoding':'one negative impression row plus one positive row for each clicked item',
        'metric_scope':'oracle expected synthetic utility; not observed production CTR'}
DIMS=[{'field':'category'},{'field':'manufacturer'}]

def sigmoid(z):return 1/(1+np.exp(-np.clip(z,-30,30)))

def make_world(seed,n,cfg=CONFIG):
    streams=[np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(4)]
    rg,users,logging,evaluation=streams
    m=cfg['products'];cats=np.arange(m)%cfg['categories'];brands=rg.integers(0,4,m)
    quality=rg.normal(0,1,m);ratings=np.clip(3.2+.55*quality,1,5)
    category_weights=rg.normal(0,1,cfg['categories']);brand_weights=rg.normal(0,.35,4)
    feature=rg.random(m)<.2;latent=rg.normal(0,.25,m)
    prefs=category_weights+users.normal(0,.65,(n,cfg['categories']))
    static=rg.normal(0,1,m)
    docs={f'p{i:03d}':{'id':f'p{i:03d}','category':f'c{cats[i]}',
        'manufacturer':f'b{brands[i]}','averageStarRating':float(ratings[i]),
        'ratingsCount':10,'is_featured':bool(feature[i])} for i in range(m)}
    return {'docs':docs,'cats':cats,'brands':brands,'quality':quality,
            'brand_weights':brand_weights,'global_category':category_weights,
            'prefs':prefs,'feature':feature,'latent':latent,'static':static,
            'logging_rng':logging,'evaluation_rng':evaluation,'n':n}

def browse_logits(world,user,drift=0.0):
    # Drift changes only future global category taste, retaining individual offsets.
    pref=world['prefs'][user]-2*drift*world['global_category']
    return (-1.3+.9*pref[world['cats']]+.65*world['quality']+
            world['brand_weights'][world['brands']]+.2*world['feature']+world['latent'])

def attraction(logits,cfg=CONFIG):
    e=cfg['click_noise'];return e+(1-2*e)*sigmoid(logits)

def simulate(seed,n,scenario,cfg=CONFIG):
    w=make_world(seed,n,cfg);rg=w['logging_rng'];s=cfg['scenarios'][scenario]
    ntrain=int(n*cfg['train_customer_fraction']);rows=[];sessions=[]
    ids=list(w['docs']);k=cfg['slate_size'];exam=1/np.log2(np.arange(k)+2)**cfg['examination_exponent']
    for step in range(cfg['train_sessions']):
        for user in range(ntrain):
            search=bool(rg.random()<s['search_share']);qcat=int(rg.integers(cfg['categories']))
            q=f'category c{qcat}' if search else ''
            base=browse_logits(w,user)
            if search:
                a=s['alignment']
                specific=-1.3+1.4*(w['cats']==qcat)-.6*w['global_category'][w['cats']]+.3*w['quality']
                logits=a*base+(1-a)*specific
            else:logits=base
            retrieval=(1-s['alignment'])*(w['cats']==qcat) if search else 0
            weights=np.exp(.35*w['static']+retrieval);weights/=weights.sum()
            slate=rg.choice(len(ids),size=k,replace=False,p=weights)
            clicks=rg.random(k)<exam*attraction(logits,cfg)[slate]
            session=f'{user}:{step}'
            sessions.append({'user':user,'session':session,'step':step,'query':q,'clicks':int(clicks.sum())})
            for rank,(item,clicked) in enumerate(zip(slate,clicks)):
                event={'doc_id':ids[item],'rank':rank,'clicked':False,'query':q,
                       'user':user,'session':session,'step':step}
                rows.append(event)
                if clicked:rows.append({**event,'clicked':True})
    return w,rows,sessions

def formula_scores(docs,rows):
    scores={key:0.0 for key in docs}
    for row in rows:
        scores[row['doc_id']]+=position_weight(row['rank'])*(1 if row['clicked'] else .1)
    return scores

def select_rows(rows,checkpoint,mode,seed):
    allrows=[r for r in rows if r['step']<checkpoint]
    browse=[r for r in allrows if r['query']=='']
    if mode=='browse':return browse
    if mode=='mixed':return allrows
    if mode=='mixed_matched':
        rg=np.random.default_rng(np.random.SeedSequence([seed,checkpoint,884]))
        chosen=np.sort(rg.choice(len(allrows),size=len(browse),replace=False))
        return [allrows[i] for i in chosen]
    raise ValueError(mode)

def policy_scores(w,rows,checkpoint,seed):
    docs=w['docs'];ids=list(docs)
    selected={m:select_rows(rows,checkpoint,m,seed) for m in ['browse','mixed','mixed_matched']}
    assert len(selected['browse'])==len(selected['mixed_matched'])
    outputs={'static':dict(zip(ids,w['static']))};counts={'static':0};fallbacks={}
    for mode in ['mixed','browse']:
        outputs['formula_'+mode]=formula_scores(docs,selected[mode]);counts['formula_'+mode]=len(selected[mode])
    for mode in ['mixed','mixed_matched','browse']:
        result=fit_scores(docs,selected[mode],DIMS)
        outputs[mode]=result if result is not None else outputs['static']
        counts[mode]=len(selected[mode]);fallbacks[mode]=result is None
    return outputs,counts,fallbacks

def ranking_metrics(probabilities,order,k=10,cfg=CONFIG):
    top=np.asarray(order[:k]);ideal=np.sort(probabilities)[::-1][:k]
    discount=1/np.log2(np.arange(k)+2)
    ndcg=float(np.dot(probabilities[top],discount)/np.dot(ideal,discount))
    exam=discount**cfg['examination_exponent']
    expected=float(np.dot(probabilities[top],exam))
    return ndcg,expected

def evaluate(w,outputs,scenario,cfg=CONFIG):
    rg=w['evaluation_rng'];ntrain=int(w['n']*cfg['train_customer_fraction']);ids=list(w['docs'])
    scores={m:np.array([v[k] for k in ids]) for m,v in outputs.items()}
    totals={(cohort,m):[] for cohort in ['seen','unseen'] for m in outputs}
    for user in range(w['n']):
        cohort='seen' if user<ntrain else 'unseen'
        p=attraction(browse_logits(w,user,cfg['scenarios'][scenario]['future_drift']),cfg)
        for _ in range(cfg['evaluation_sessions']):
            candidates=rg.choice(len(ids),cfg['evaluation_candidates'],replace=False)
            for m in outputs:
                order=np.lexsort((candidates,-scores[m][candidates]))
                totals[cohort,m].append(ranking_metrics(p[candidates],order,cfg['slate_size'],cfg))
    return {key:np.mean(value,axis=0).tolist() for key,value in totals.items()}

def run_study(out=OUT,cfg=CONFIG):
    out.mkdir(parents=True,exist_ok=True);(out/'data').mkdir(exist_ok=True)
    (out/'data/design.json').write_text(json.dumps(cfg,indent=2))
    results=[];start=time.time()
    for n in cfg['customers']:
        for scenario in cfg['scenarios']:
            for seed in cfg['seeds']:
                w,rows,sessions=simulate(seed,n,scenario,cfg)
                # Same held-out candidate slates at every checkpoint and for all methods.
                state=w['evaluation_rng'].bit_generator.state
                for checkpoint in cfg['checkpoints']:
                    outputs,counts,fallbacks=policy_scores(w,rows,checkpoint,seed)
                    w['evaluation_rng'].bit_generator.state=state
                    measured=evaluate(w,outputs,scenario,cfg)
                    for (cohort,method),(ndcg,utility) in measured.items():
                        results.append({'customers':n,'scenario':scenario,'seed':seed,'checkpoint':checkpoint,
                            'cohort':cohort,'method':method,'ndcg10':ndcg,'expected_clicks10':utility,
                            'train_rows':counts[method],'fallback':fallbacks.get(method,False)})
                if n==200 and scenario=='shifted' and seed==0:
                    with gzip.open(out/'data/example_synthetic_events.jsonl.gz','wt') as f:
                        for row in rows:f.write(json.dumps(row)+'\n')
                    (out/'data/example_synthetic_catalog.json').write_text(json.dumps(w['docs'],indent=2))
                    (out/'data/example_sessions.json').write_text(json.dumps(sessions))
            print(f'Completed {n} customers / {scenario}',flush=True)
    with (out/'data/run_metrics.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    import sklearn
    manifest={'synthetic':True,'worlds':len(cfg['customers'])*len(cfg['scenarios'])*len(cfg['seeds']),
              'model_fits':len(cfg['customers'])*len(cfg['scenarios'])*len(cfg['seeds'])*len(cfg['checkpoints'])*3,
              'runtime_seconds':time.time()-start,'python':platform.python_version(),
              'numpy':np.__version__,'scikit_learn':sklearn.__version__,
              'design_sha256':hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest(),
              'core_sha256':hashlib.sha256((ROOT/'backend/src/isp/services/domain/popularity_model.py').read_bytes()).hexdigest(),
              'study_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out/'data/manifest.json').write_text(json.dumps(manifest,indent=2))
    return results

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=OUT)
    args=parser.parse_args();run_study(args.out)
