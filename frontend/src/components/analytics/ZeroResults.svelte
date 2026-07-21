<script lang="ts">
  import type { ZeroResultQuery } from '../../lib/types';

  export let queries: ZeroResultQuery[] = [];
</script>

<div class="card">
  <div class="card-header">
    <span class="card-title">Zero Results</span>
    {#if queries.length > 0}
      <span class="badge">{queries.length} queries returning nothing</span>
    {/if}
  </div>
  {#if queries.length === 0}
    <div class="empty good">All searches returned results ✓</div>
  {:else}
    <table class="table">
      <thead>
        <tr>
          <th>#</th>
          <th>Query</th>
          <th class="num">Times searched</th>
        </tr>
      </thead>
      <tbody>
        {#each queries as q, i}
          <tr>
            <td class="rank">{i + 1}</td>
            <td class="query">{q.query}</td>
            <td class="num">{q.count.toLocaleString()}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    <p class="hint">Add synonyms or new content to fix these gaps.</p>
  {/if}
</div>

<style>
  .card        { background:var(--bg-2); border:1px solid var(--border); border-radius:10px; padding:20px; }
  .card-header { display:flex; align-items:baseline; justify-content:space-between; margin-bottom:14px; }
  .card-title  { font-size:13px; font-weight:600; color:var(--text); }
  .badge       { font-size:11px; background:var(--danger-bg); color:var(--danger-text);
                 border:1px solid var(--danger-border); border-radius:20px; padding:2px 8px; }
  .empty       { font-size:12px; color:var(--text-6); text-align:center; padding:32px 0; }
  .good        { color:#16a34a; }
  .table       { width:100%; border-collapse:collapse; font-size:12px; }
  .table th    { text-align:left; color:var(--text-6); font-weight:600; font-size:11px;
                 text-transform:uppercase; letter-spacing:0.05em; padding:0 8px 8px 0;
                 border-bottom:1px solid var(--border); }
  .table td    { padding:8px 8px 8px 0; border-bottom:1px solid var(--border-2); color:var(--text-3); }
  .table tr:last-child td { border-bottom:none; }
  .rank        { color:var(--text-6); width:28px; }
  .query       { font-weight:500; color:var(--danger-text); }
  .num         { text-align:right; font-variant-numeric:tabular-nums; }
  .hint        { margin:12px 0 0; font-size:11px; color:var(--text-6); }
</style>
