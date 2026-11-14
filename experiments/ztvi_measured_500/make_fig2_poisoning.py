"""New Figure 2: poisoning successes per round with 95% error bars, from saved results only.

Per-round bars: Wilson 95% interval on k/110 (one answer per case per round), shown as counts.
'Mean' column: five-round mean with t interval (df = 4), same values as Table 3.
Usage: make_fig2.py <analysis_v2.json> <out.png>
"""
import json, math, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

INK, MUTED, GRID = '#2b2b2b', '#6b6b6b', '#e4e4e4'
N, Z = 110, 1.96
A = json.load(open(sys.argv[1]))['arms']


def wilson(k):
    p = k / N
    d = 1 + Z * Z / N
    c = (p + Z * Z / (2 * N)) / d
    h = Z * math.sqrt(p * (1 - p) / N + Z * Z / (4 * N * N)) / d
    return k - (c - h) * N, (c + h) * N - k


series = [('SI', 'Shared index', '#d9574f', 'o'), ('NF', 'Namespace filter', '#c9920c', 's'),
          ('PG', 'Policy-gated', '#2aa198', '^'), ('FV', 'Zero-trust isolation', '#6a4fb5', 'D')]
off = {'SI': -.21, 'NF': -.07, 'PG': .07, 'FV': .21}

fig, ax = plt.subplots(figsize=(3.45, 2.25))
for s in ('top', 'right'):
    ax.spines[s].set_visible(False)
for s in ('left', 'bottom'):
    ax.spines[s].set_color(MUTED); ax.spines[s].set_linewidth(.6)
ax.tick_params(colors=MUTED, labelsize=6.5, width=.6, length=2.5)
ax.yaxis.grid(True, color=GRID, lw=.5); ax.set_axisbelow(True)

for a, label, c, m in series:
    ys = A[a]['any']
    xs = [r + off[a] for r in range(1, 6)]
    err = list(zip(*[wilson(k) for k in ys]))
    ax.errorbar(xs, ys, yerr=err, color=c, marker=m, ms=3, lw=1.0, elinewidth=.6, capsize=1.5, capthick=.6,
                label=label, zorder=3)
    s = A[a]['any_summary']
    ax.errorbar([6.4 + off[a]], [s['mean']], yerr=[[s['mean'] - s['low']], [s['high'] - s['mean']]], color=c, marker=m,
                ms=3.5, elinewidth=.9, capsize=2, capthick=.9, ls='none', zorder=3)

ax.axvline(5.7, color=GRID, lw=.8)
ax.set_xticks([1, 2, 3, 4, 5, 6.4]); ax.set_xticklabels(['1', '2', '3', '4', '5', 'Mean'])
ax.set_xlim(.6, 6.8); ax.set_ylim(0, 65)
ax.set_xlabel('Test round', fontsize=7, color=INK)
ax.set_ylabel('Poisoning successes (of 110)', fontsize=7, color=INK)
fig.legend(*ax.get_legend_handles_labels(), loc='upper center', fontsize=6, ncol=2, frameon=False, labelcolor=INK,
           handletextpad=.4, columnspacing=1.2)
fig.tight_layout(pad=.4, rect=(0, 0, 1, .86))
fig.savefig(sys.argv[2], dpi=400)
