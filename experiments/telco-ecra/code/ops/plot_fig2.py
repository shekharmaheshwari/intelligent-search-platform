"""Fig. 2 of paper v17 from ops/data/summary_1000.json: correct answers (%) per answer across A1-A6 on the 800 test
cases (means over runs). Same layout as the earlier figure."""
import json
import os
import sys
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR', '/tmp/telco-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
S = json.loads((HERE.parent / 'ops/data/summary_1000.json').read_text())
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / 'image2.png'
N = S['n_test']
ARMS = [('A1', 'A1_4o_r1'), ('A2', 'A2_r1_low'), ('A3', 'programs_R1'), ('A4', 'A4_r2_low'), ('A5', 'A5_r2_medium_ex'), ('A6', 'programs_R2')]
SERIES = [('plan_price', 'Plan Price', '#1f5bd6', 'o', '-'), ('net_billed', 'Net Billed Amount', '#1a7a1a', 's', '--'),
          ('promotion_eligible', 'Promotion Eligibility', '#e8141c', '^', '-.'), ('support_correct', 'Support Statement', '#6a1b9a', 'D', ':')]
plt.rcParams.update({'font.family': 'serif', 'font.serif': ['Times New Roman', 'Times', 'DejaVu Serif']})
fig, ax = plt.subplots(figsize=(9.6, 6.2))
vals = {}
for key, label, col, mk, ls in SERIES:
    y = [100 * sum(r['per_answer'][key]['correct'] for r in S['arms'][a]) / len(S['arms'][a]) / N for _, a in ARMS]
    vals[key] = y
    ax.plot(range(6), y, color=col, marker=mk, ls=ls, lw=2, ms=8, label=label)
lo = min(min(v) for v in vals.values())
fmt = lambda v: '100' if round(v, 1) == 100 else f'{v:.1f}'
# label offsets (points) per approach: plan price above; the rest placed so that no two labels meet
OFF = {'promotion_eligible': [(0, -16), (0, 9), (0, -16), (0, -16), (-26, -3), None],
       'net_billed': [(0, -16), (16, -14), (16, -14), (0, 9), (16, -14), None],
       'support_correct': [(0, -16), None, None, None, None, None]}
COL = {'net_billed': '#1a7a1a', 'promotion_eligible': '#e8141c', 'support_correct': '#6a1b9a'}
for i in range(6):
    v = vals['plan_price'][i]
    ax.annotate(fmt(v), (i, v), textcoords='offset points', xytext=(0, 9), ha='center', fontsize=11, color='#1f5bd6')
    merged = abs(vals['net_billed'][i] - vals['support_correct'][i]) < 0.05
    for k, offs in OFF.items():
        if offs[i] is None:
            continue
        v = vals[k][i]
        col = '#222222' if (k == 'net_billed' and merged) else COL[k]
        ax.annotate(fmt(v), (i, v), textcoords='offset points', xytext=offs[i], ha='center', fontsize=11, color=col)
ax.set_xticks(range(6), [a for a, _ in ARMS], fontsize=12); ax.tick_params(axis='y', labelsize=12)
ax.set_ylim(max(0, 10 * int(lo // 10) - 5), 104)
ax.set_xlabel('Evaluated Approach (in the order run)', fontsize=15); ax.set_ylabel('Correct answers (%)', fontsize=15)
ax.grid(ls='--', alpha=.4); ax.legend(loc='lower right', fontsize=13)
fig.tight_layout(); fig.savefig(OUT, dpi=150); print('wrote', OUT, {k: [round(x, 1) for x in v] for k, v in vals.items()})
