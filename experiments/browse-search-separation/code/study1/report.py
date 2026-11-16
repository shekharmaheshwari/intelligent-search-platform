"""Aggregate all seeds, paired bootstrap intervals, and paper figures."""
import csv
import json
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/isp-paper-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from study import OUT,CONFIG

LABELS={'static':'Static','formula_mixed':'Formula: mixed','formula_browse':'Formula: browse',
        'mixed':'LR: mixed','mixed_matched':'LR: mixed, matched','browse':'LR: browse'}
SCENARIOS={'aligned':'Aligned','shifted':'Shifted','sparse_browse':'Sparse browse','preference_drift':'Future drift'}

def bootstrap(values):
    v=np.asarray(values);rng=np.random.default_rng(813)
    estimates=v[rng.integers(0,len(v),(5000,len(v)))].mean(axis=1)
    return float(v.mean()),*[float(z) for z in np.percentile(estimates,[2.5,97.5])]

def main():
    raw=list(csv.DictReader((OUT/'data/run_metrics.csv').open()))
    def values(n,scenario,checkpoint,cohort,method,metric):
        rows=[r for r in raw if int(r['customers'])==n and r['scenario']==scenario and int(r['checkpoint'])==checkpoint and r['cohort']==cohort and r['method']==method]
        rows.sort(key=lambda r:int(r['seed']))
        assert [int(r['seed']) for r in rows]==CONFIG['seeds']
        return np.array([float(r[metric]) for r in rows])
    assert not any(r['fallback']=='True' for r in raw)
    summaries=[];deltas=[]
    for n in CONFIG['customers']:
        for scenario in SCENARIOS:
            for cohort in ['seen','unseen']:
                for method in CONFIG['methods']:
                    stats=bootstrap(values(n,scenario,20,cohort,method,'ndcg10'))
                    utility=values(n,scenario,20,cohort,method,'expected_clicks10')
                    summaries.append({'customers':n,'scenario':scenario,'cohort':cohort,'method':method,
                                      'ndcg_mean':stats[0],'ndcg_low':stats[1],'ndcg_high':stats[2],
                                      'expected_clicks_mean':float(utility.mean())})
                for base in ['mixed_matched','mixed','static']:
                    a=values(n,scenario,20,cohort,'browse','ndcg10');b=values(n,scenario,20,cohort,base,'ndcg10')
                    mean,lo,hi=bootstrap(a-b)
                    deltas.append({'customers':n,'scenario':scenario,'cohort':cohort,'comparison':'browse-minus-'+base,
                                   'mean':mean,'low':lo,'high':hi,'positive_seeds':int(sum(a>b)),'seeds':len(a)})
    for filename,rows in [('summary',summaries),('paired_differences',deltas)]:
        with (OUT/f'data/{filename}.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    primary=[r for r in deltas if r['cohort']=='unseen' and r['comparison']=='browse-minus-mixed_matched']
    (OUT/'data/primary_results.json').write_text(json.dumps(primary,indent=2))
    tex='\\begin{tabular}{lrrrr}\n\\toprule\nMethod & Aligned & Shifted & Sparse & Drift\\\\\n\\midrule\n'
    for method in CONFIG['methods']:
        tex+=LABELS[method]+' & '+' & '.join(f"{values(200,s,20,'unseen',method,'ndcg10').mean():.3f}" for s in SCENARIOS)+'\\\\\n'
    tex+='\\bottomrule\n\\end{tabular}\n';(OUT/'tables/ndcg.tex').write_text(tex)
    tex='\\begin{tabular}{llrr}\n\\toprule\nUsers & Scenario & $\\Delta$NDCG & 95\\% interval\\\\\n\\midrule\n'
    for r in primary:tex+=f"{r['customers']} & {SCENARIOS[r['scenario']]} & {r['mean']:+.3f} & [{r['low']:+.3f}, {r['high']:+.3f}]\\\\\n"
    tex+='\\bottomrule\n\\end{tabular}\n';(OUT/'tables/differences.tex').write_text(tex)
    tex='\\begin{tabular}{lrrr}\n\\toprule\nScenario & Static & Mixed, matched & Browse\\\\\n\\midrule\n'
    for s in SCENARIOS:tex+=SCENARIOS[s]+' & '+' & '.join(f"{values(200,s,20,'unseen',m,'expected_clicks10').mean():.3f}" for m in ['static','mixed_matched','browse'])+'\\\\\n'
    tex+='\\bottomrule\n\\end{tabular}\n';(OUT/'tables/utility.tex').write_text(tex)
    plt.rcParams.update({'font.size':8,'pdf.fonttype':42,'axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(figsize=(3.5,2.65))
    for j,n in enumerate([100,200]):
        r=[r for r in primary if r['customers']==n];y=np.arange(4)+(j-.5)*.18
        mean=np.array([x['mean'] for x in r]);low=np.array([x['low'] for x in r]);high=np.array([x['high'] for x in r])
        ax.errorbar(mean,y,xerr=[mean-low,high-mean],fmt='o',markersize=4,capsize=3,label=f'{n} customers')
    ax.axvline(0,color='gray',linestyle='--',linewidth=.8);ax.set_yticks(range(4),list(SCENARIOS.values()))
    ax.invert_yaxis();ax.set_xlabel('Browse minus matched mixed: NDCG@10');ax.legend(fontsize=7,loc='best')
    fig.tight_layout()
    for ext in ['pdf','png']:fig.savefig(OUT/f'figures/paired_effects.{ext}',bbox_inches='tight',dpi=200)
    plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(7,4),sharex=True,sharey=True)
    for ax,s in zip(axes.flat,SCENARIOS):
        before=values(200,s,20,'unseen','static','ndcg10').mean()
        for m in ['browse','mixed','mixed_matched']:
            ys=[before]+[values(200,s,c,'unseen',m,'ndcg10').mean() for c in [5,20]]
            ax.plot([0,5,20],ys,'o-',markersize=3,label=LABELS[m])
        ax.set_title(SCENARIOS[s]);ax.set_xticks([0,5,20]);ax.set_ylim(.35,1.0)
        ax.set_xlabel('Training sessions per seen customer');ax.set_ylabel('NDCG@10')
    axes[0,0].legend(fontsize=7);fig.tight_layout()
    for ext in ['pdf','png']:fig.savefig(OUT/f'figures/before_after.{ext}',bbox_inches='tight',dpi=200)
    plt.close(fig)
    text=['# Measured simulation comparison','', '**All data are synthetic.** 20 seed-level replications per configuration.','',
          '| Customers | Scenario | Browse − matched mixed NDCG | Pointwise 95% bootstrap interval |',
          '|---:|---|---:|---|']
    for r in primary:text.append(f"| {r['customers']} | {r['scenario']} | {r['mean']:+.4f} | [{r['low']:+.4f}, {r['high']:+.4f}] |")
    text+=['','Intervals resample paired seeds, not events. They describe simulator variability, not uncertainty about real customers.','',
           'The before/after figure compares a fixed static policy with models trained after 5 and 20 sessions on the same held-out candidate sets. No deployment occurred.']
    (OUT/'RESULTS.md').write_text('\n'.join(text)+'\n')
    print(json.dumps(primary,indent=2))

if __name__=='__main__':main()
