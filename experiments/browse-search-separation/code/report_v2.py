"""Tables and figures for paper v2 from the v1 simulation, Study A (KuaiSAR), and Study B (ISP agents)."""
import json
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR', '/tmp/isp-paper-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / 'docs/research-papers/browse-search-separation'
OUT = ROOT / 'docs/research-papers' / os.environ.get('PAPER_VERSION', 'browse-search-separation-v2')
SCEN = {'aligned': 'Aligned', 'shifted': 'Shifted', 'sparse_browse': 'Sparse browse', 'preference_drift': 'Future drift'}
LABELS = {'static': 'Static', 'random': 'Random', 'search': 'LR: search only', 'formula_mixed': 'Formula: mixed',
          'formula_browse': 'Formula: browse', 'mixed': 'LR: mixed', 'mixed_matched': 'LR: mixed, matched',
          'browse': 'LR: browse'}
COND = {'full': 'Full browse', 'sparse_browse': 'Sparse browse (10\\%)', 'sparse_constant_mix': 'Sparse, constant mix (10\\%)',
        'sparse_browse_1pct': 'Sparse browse (1\\%)', 'sparse_constant_mix_1pct': 'Sparse, constant mix (1\\%)'}


def write(name, header, cols, body):
    tex = f"\\begin{{tabular}}{{{cols}}}\n\\toprule\n{header}\\\\\n\\midrule\n" + body + '\\bottomrule\n\\end{tabular}\n'
    (OUT / f'tables/{name}.tex').write_text(tex)


def sim():
    primary = json.loads((V1 / 'data/primary_results.json').read_text())
    write('sim_differences', 'Users & Scenario & $\\Delta$NDCG & 95\\% interval', 'llrr',
          ''.join(f"{r['customers']} & {SCEN[r['scenario']]} & {r['mean']:+.4f} & [{r['low']:+.4f}, {r['high']:+.4f}]\\\\\n"
                  for r in primary))
    m = pd.read_csv(V1 / 'data/run_metrics.csv')
    m = m[(m.customers == 200) & (m.cohort == 'unseen')]
    fig, axes = plt.subplots(1, 4, figsize=(7.2, 2.0), sharey=True)
    for ax, s in zip(axes, SCEN):
        g = m[m.scenario == s]
        before = g[(g.method == 'static') & (g.checkpoint == 20)].ndcg10.mean()
        for meth in ['browse', 'mixed', 'mixed_matched']:
            ys = [before] + [g[(g.method == meth) & (g.checkpoint == c)].ndcg10.mean() for c in [5, 20]]
            ax.plot([0, 5, 20], ys, 'o-', markersize=3, label=LABELS[meth])
        ax.set_title(SCEN[s], fontsize=8); ax.set_xticks([0, 5, 20]); ax.set_ylim(.4, 1.0)
        ax.set_xlabel('Sessions per customer', fontsize=7)
    axes[0].set_ylabel('NDCG@10'); axes[0].legend(fontsize=6, loc='lower right')
    fig.tight_layout()
    fig.savefig(OUT / 'figures/sim_before_after.pdf', bbox_inches='tight')
    plt.close(fig)
    return [r for r in primary if r['customers'] == 200]


def kuaisar():
    m = pd.read_csv(OUT / 'data/kuaisar_metrics.csv')
    m = m[m.cohort == 'unseen'].pivot(index='method', columns='condition', values='ndcg')
    order = ['random', 'search', 'mixed', 'mixed_matched', 'browse']
    conds = [c for c in COND if c in m.columns]
    short = {'full': 'Full', 'sparse_browse': '10\\%', 'sparse_constant_mix': '10\\%, mix', 'sparse_browse_1pct': '1\\%',
             'sparse_constant_mix_1pct': '1\\%, mix'}
    write('kuaisar_ndcg', 'Method & ' + ' & '.join(short[c] for c in conds), 'l' + 'r' * len(conds),
          ''.join(f"{LABELS[k]} & " + ' & '.join(f"{m.loc[k, c]:.4f}" for c in conds) + "\\\\\n" for k in order))
    d = pd.read_csv(OUT / 'data/kuaisar_paired_differences.csv')
    d = d[(d.cohort == 'unseen') & (d.comparison == 'browse - mixed_matched')]
    lost = {c: (m.loc['browse', c] - m.loc['mixed_matched', c]) / (m.loc['browse', c] - m.loc['random', c])
            for c in conds}
    raw = pd.read_csv(OUT / 'data/kuaisar_metrics.csv'); raw = raw[raw.cohort == 'unseen'].set_index(['method', 'condition'])
    share = {c: 1 - raw.loc[('browse', c), 'train_rows'] / raw.loc[('mixed', c), 'train_rows'] for c in conds}
    d = d.set_index('condition').loc[conds].reset_index()
    label = {'full': 'All', 'sparse_browse': '10\\%', 'sparse_constant_mix': '10\\%, mixture fixed',
             'sparse_browse_1pct': '1\\%', 'sparse_constant_mix_1pct': '1\\%, mixture fixed'}
    write('kuaisar_differences', 'Browse rows kept & Search share & $\\Delta$NDCG & 95\\% interval & Gain lost', 'lrrrr',
          ''.join(f"{label[r.condition]} & {100 * share[r.condition]:.0f}\\% & {r.delta:+.4f} & "
                  f"[{r.ci_low:+.4f}, {r.ci_high:+.4f}] & {100 * lost[r.condition]:.0f}\\%\\\\\n" for r in d.itertuples()))
    return d, lost


def agents():
    f = OUT / 'data/isp_metrics.csv'
    if not f.exists():
        return None
    m = pd.read_csv(f)
    final = max(m.checkpoint)
    g = m[m.checkpoint == final].groupby(['method', 'condition']).ndcg10.mean().unstack()
    post = pd.read_csv(OUT / 'data/isp_posthoc_rescoring.csv')
    rnd = post[(post['mode'] == 'all') & (post.method == 'random')].set_index('condition').ndcg
    g.loc['random'] = rnd
    order = ['random', 'static', 'formula_mixed', 'formula_browse', 'mixed', 'mixed_matched', 'browse']
    labels = {**LABELS, 'random': 'Random (expected)', 'static': 'Static (one random priority)'}
    write('agents_ndcg', 'Method & Full browse & Sparse browse', 'lrr',
          ''.join(f"{labels[k]} & {g.loc[k, 'full']:.3f} & {g.loc[k, 'sparse_browse']:.3f}\\\\\n" for k in order))
    modes = {'all': 'All graded items', 'browse_exposed': 'Browse-exposed items', 'within_category': 'Within category'}
    body = ''
    for mode, name in modes.items():
        for cond in ['full', 'sparse_browse']:
            p = post[(post['mode'] == mode) & (post.condition == cond)].set_index('method')
            d = p.loc['browse - mixed_matched']
            body += (f"{name} & {COND[cond].split(' (')[0]} & {p.loc['random', 'ndcg']:.4f} & {p.loc['browse', 'ndcg']:.3f} & "
                     f"{p.loc['mixed_matched', 'ndcg']:.3f} & {d.ndcg:+.3f} [{d.ci_low:+.3f}, {d.ci_high:+.3f}]\\\\\n")
    write('agents_rescoring', 'Scoring & Browse data & Random & Browse & Matched & $\\Delta$ [95\\% CI]', 'llrrrl', body)
    d = pd.read_csv(OUT / 'data/isp_paired_differences.csv')
    write('agents_differences', 'Condition & Sessions & Comparison & $\\Delta$NDCG & 95\\% interval', 'lrlrr',
          ''.join(f"{COND[r.condition]} & {r.checkpoint} & {r.comparison.replace('mixed_matched', 'matched').replace(' - ', ' $-$ ')} & "
                  f"{r.delta:+.3f} & [{r.ci_low:+.3f}, {r.ci_high:+.3f}]\\\\\n" for r in d.itertuples()))
    s = json.loads((OUT / 'data/isp_summary.json').read_text())['realism']
    ne = json.loads((OUT / 'data/isp_v4_posthoc.json').read_text())['session_stats_nonempty']
    s['clicks_per_session'] = {t: ne[t]['clicks_per_session'] for t in ne}
    s['zero_click_session_share'] = {t: ne[t]['zero_click_share'] for t in ne}
    k = json.loads((OUT / 'data/kuaisar_realism.json').read_text())
    rows = [('Impression CTR', s['impression_ctr']['browse'], k['browse']['ctr'], s['impression_ctr']['search'], k['search']['ctr']),
            ('Clicks per session', s['clicks_per_session']['browse'], k['browse']['clicks'], s['clicks_per_session']['search'], k['search']['clicks']),
            ('Zero-click sessions', s['zero_click_session_share']['browse'], k['browse']['zero'], s['zero_click_session_share']['search'], k['search']['zero'])]
    write('realism', 'Statistic & \\multicolumn{2}{c}{Browse} & \\multicolumn{2}{c}{Search}\\\\\n & Agents & KuaiSAR & Agents & KuaiSAR', 'lrrrr',
          ''.join(f"{n} & {a:.3f} & {b:.3f} & {c:.3f} & {e:.3f}\\\\\n" for n, a, b, c, e in rows))
    ranks = s['ctr_by_rank']
    fig, ax = plt.subplots(figsize=(3.4, 1.9))
    ax.bar([int(r) + 1 for r in ranks], list(ranks.values()), color='#4c72b0')
    ax.set_xlabel('Displayed position'); ax.set_ylabel('Agent CTR'); ax.set_xticks(range(1, 11))
    for side in ['top', 'right']:
        ax.spines[side].set_visible(False)
    fig.tight_layout(); fig.savefig(OUT / 'figures/agent_position_ctr.pdf', bbox_inches='tight'); plt.close(fig)
    # Exposure arms: same personas, search sessions, and grading sets; only browse exposure differs.
    arms = pd.read_csv(OUT / 'data/exposure_arms.csv')
    two = json.loads((OUT / 'data/exposure_two_level.json').read_text())
    vol = json.loads((OUT / 'data/exposure_volume_control.json').read_text())
    names = {'default': 'Default order', 'shuffledhead': 'Shuffled head', 'randbrowse': 'Whole category'}
    ci = lambda v: f"[{v[0]:+.3f}, {v[1]:+.3f}]"
    body = ''
    for arm, name in names.items():
        for cond in ['full', 'sparse_browse']:
            r = arms[(arms.arm == arm) & (arms.condition == cond)].iloc[0]
            body += (f"{name} & {int(r.browse_products_shown)} & {COND[cond].split(' (')[0]} & {int(r.browse_rows)} & "
                     f"{r.delta:+.3f} {ci(json.loads(r.ci))}\\\\\n")
    for cond in ['full', 'sparse_browse']:
        v = vol[cond]
        body += (f"Whole category, row-matched & 572 & {COND[cond].split(' (')[0]} & {v['target_browse_rows']} & "
                 f"{v['delta']:+.3f} {ci(v['ci'])}\\\\\n")
    body += '\\midrule\n'
    for arm in ['shuffledhead', 'randbrowse']:
        for cond in ['full', 'sparse_browse']:
            r = arms[(arms.arm == f'{arm} - default') & (arms.condition == cond)].iloc[0]
            t = two[cond][f'{arm} - default']
            body += (f"\\multicolumn{{2}}{{l}}{{{names[arm]} $-$ default}} & {COND[cond].split(' (')[0]} & & "
                     f"{r.delta:+.3f} {ci(json.loads(r.ci))}; two-level {t['mean']:+.3f} {ci(t['ci'])}\\\\\n")
    split = json.loads((OUT / 'data/isp_v4_posthoc.json').read_text())['loss_by_exposure']
    sub = {'browse_shown': 'Shown on browse', 'unshown_manufacturer_seen': 'Not shown; manufacturer seen',
           'unshown_manufacturer_unseen': 'Not shown; manufacturer unseen'}
    write('agents_exposure_split', 'Graded products & Browse data & Sets & Items & $\\Delta$ [95\\% CI]', 'llrrl',
          ''.join(f"{sub[r['subset']]} & {COND[r['condition']].split(' (')[0]} & {r['groups']} & {r['mean_items']:.1f} & "
                  f"{r['delta']:+.3f} [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]\\\\\n" for r in split))
    write('agents_intervention', 'Browse exposure & Shown & Browse data & Rows & $\\Delta$ [95\\% CI]', 'lrlrl', body)
    return d[(d.checkpoint == final) & (d.comparison == 'browse - mixed_matched')]


def kuaisar_realism():
    f = OUT / 'data/kuaisar_realism.json'
    if f.exists():
        return
    e = pd.read_parquet(ROOT / 'experiments/browse_search_separation_v2/data_cache/events.parquet',
                        columns=['source', 'request', 'click', 'user_id'])
    out = {}
    for s in ['browse', 'search']:
        g = e[e.source == s].groupby(['user_id', 'request']).click.agg(['sum', 'size'])
        out[s] = {'ctr': float(e[e.source == s].click.mean()), 'clicks': float(g['sum'].mean()),
                  'zero': float((g['sum'] == 0).mean()), 'items': float(g['size'].mean())}
    f.write_text(json.dumps(out, indent=2))


def forest(simrows, kd, ad):
    """One panel per study, each on its own scale: the metrics and baselines differ, so magnitudes are not compared."""
    panels = [('Study 1: simulation', [(SCEN[r['scenario']], r['mean'], r['low'], r['high']) for r in simrows], '#4c72b0'),
              ('Study 2: KuaiSAR', [(COND[r.condition].replace('\\', ''), r.delta, r.ci_low, r.ci_high) for r in kd.itertuples()], '#dd8452')]
    if ad is not None:
        ea = pd.read_csv(OUT / 'data/exposure_arms.csv'); ea = ea[~ea.arm.str.contains(' - ')]
        lab = {'default': 'Default', 'shuffledhead': 'Shuffled head', 'randbrowse': 'Whole category'}
        items = [(f"{lab[r.arm]}, {'full' if r.condition == 'full' else 'sparse'}", r.delta, *json.loads(r.ci))
                 for r in ea.itertuples()]
        panels.append(('Study 3: LLM shopper agents', items, '#55a868'))
    heights = [len(p[1]) for p in panels]
    fig, axes = plt.subplots(len(panels), 1, figsize=(3.5, 0.3 * sum(heights) + 0.55 * len(panels) + 0.3),
                             gridspec_kw={'height_ratios': heights})
    for ax, (title, items, c) in zip(axes, panels):
        for i, (lab, m, lo, hi) in enumerate(items):
            ax.errorbar(m, i, xerr=[[m - lo], [hi - m]], fmt='o', color=c, markersize=4, capsize=2)
        ax.set_yticks(range(len(items)), [x[0] for x in items], fontsize=7)
        ax.axvline(0, color='gray', ls='--', lw=.8); ax.invert_yaxis(); ax.set_ylim(len(items) - .5, -.5)
        ax.set_title(title, fontsize=8, loc='left'); ax.tick_params(axis='x', labelsize=7)
        for side in ['top', 'right']:
            ax.spines[side].set_visible(False)
    axes[-1].set_xlabel('Browse minus matched pooled NDCG (scale differs by panel)', fontsize=7)
    fig.tight_layout(); fig.savefig(OUT / 'figures/effects_all.pdf', bbox_inches='tight'); plt.close(fig)


if __name__ == '__main__':
    kuaisar_realism()
    simrows = sim(); kd, lost = kuaisar(); ad = agents()
    forest(simrows, kd, ad)
    print('share of gain lost', lost); print(ad)
