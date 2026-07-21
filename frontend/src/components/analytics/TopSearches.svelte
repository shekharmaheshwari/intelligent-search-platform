<script lang="ts">
  import type { TopQuery } from '../../lib/types';

  export let queries: TopQuery[] = [];
</script>

<div class="card">
  <div class="card-header">
    <span class="card-title">Top Searches</span>
    <span class="card-sub">{queries.length} queries</span>
  </div>
  {#if queries.length === 0}
    <div class="empty">No search data yet</div>
  {:else}
    <table class="table">
      <thead>
        <tr>
          <th>#</th>
          <th>Query</th>
          <th class="num">Searches</th>
          <th class="num">Avg Results</th>
        </tr>
      </thead>
      <tbody>
        {#each queries as q, i}
          <tr>
            <td class="rank">{i + 1}</td>
            <td class="query">{q.query}</td>
            <td class="num">{q.count.toLocaleString()}</td>
            <td class="num" class:low={q.avg_results < 3}>{q.avg_results.toFixed(1)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .card        { background:var(--bg-2); border:1px solid var(--border); border-radius:10px; padding:20px; }
  .card-header { display:flex; align-items:baseline; justify-content:space-between; margin-bottom:14px; }
  .card-title  { font-size:13px; font-weight:600; color:var(--text); }
  .card-sub    { font-size:12px; color:var(--text-6); }
  .empty       { font-size:12px; color:var(--text-6); text-align:center; padding:32px 0; }
  .table       { width:100%; border-collapse:collapse; font-size:12px; }
  .table th    { text-align:left; color:var(--text-6); font-weight:600; font-size:11px;
                 text-transform:uppercase; letter-spacing:0.05em; padding:0 8px 8px 0;
                 border-bottom:1px solid var(--border); }
  .table td    { padding:8px 8px 8px 0; border-bottom:1px solid var(--border-2); color:var(--text-3); }
  .table tr:last-child td { border-bottom:none; }
  .rank        { color:var(--text-6); width:28px; }
  .query       { font-weight:500; color:var(--text-2); }
  .num         { text-align:right; font-variant-numeric:tabular-nums; }
  .low         { color:var(--danger-text); }
</style>
