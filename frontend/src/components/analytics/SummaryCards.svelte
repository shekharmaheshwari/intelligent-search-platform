<script lang="ts">
  import type { AnalyticsSummary } from '../../lib/types';

  export let summary: AnalyticsSummary;

  function fmt(n: number): string { return n.toLocaleString(); }
  function pct(n: number): string { return (n * 100).toFixed(1) + '%'; }
</script>

<div class="summary-row">
  <div class="stat-card">
    <span class="stat-label">Total Searches</span>
    <span class="stat-value">{fmt(summary.total_searches)}</span>
  </div>

  <div class="stat-card">
    <span class="stat-label">Unique Queries</span>
    <span class="stat-value">{fmt(summary.unique_queries)}</span>
  </div>

  <div class="stat-card">
    <span class="stat-label">CTR</span>
    <span class="stat-value" class:good={summary.ctr >= 0.3}>{pct(summary.ctr)}</span>
    <span class="stat-sub">click-through rate</span>
  </div>

  <div class="stat-card">
    <span class="stat-label">No-Click Rate</span>
    <span class="stat-value" class:danger={summary.zero_result_rate > 0.5}>{pct(summary.zero_result_rate)}</span>
    <span class="stat-sub">searches with no click</span>
  </div>
</div>

<style>
  .summary-row { display:grid; grid-template-columns:repeat(4, 1fr); gap:14px; }

  .stat-card   { background:var(--bg-2); border:1px solid var(--border); border-radius:10px;
                 padding:16px 18px; display:flex; flex-direction:column; gap:4px; }
  .stat-label  { font-size:11px; color:var(--text-6); text-transform:uppercase;
                 letter-spacing:0.05em; font-weight:600; }
  .stat-value  { font-size:22px; font-weight:700; color:var(--text); font-variant-numeric:tabular-nums; }
  .stat-value.danger { color:var(--danger-text); }
  .stat-value.good   { color:#22c55e; }
  .stat-sub    { font-size:10px; color:var(--text-7); }

  @media (max-width:900px) {
    .summary-row { grid-template-columns:repeat(2, 1fr); }
  }
</style>
