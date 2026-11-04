<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  /**
   * AIOverview — streams a short, cited AI summary of the current top search hits.
   *
   * Self-contained: give it the query and the hit ids (in rank order) and it
   * requests /search/overview whenever they change, aborting any stream still
   * running. [n] citations scroll to the matching result card (id="result-<docId>").
   * Hides itself when the backend skips the query (too short, navigational, no hits).
   */
  import { onDestroy } from 'svelte';
  import { streamSearchOverview, type OverviewEvent } from '../../lib/api';

  export let indexId: string;
  export let query: string;
  export let docIds: string[];

  type Source = { n: number; id: string; title: string; url: string };

  let text = '';
  let sources: Source[] = [];
  let state: 'idle' | 'loading' | 'streaming' | 'done' | 'skipped' | 'error' = 'idle';
  let errMsg = '';
  let cached = false;
  let controller: AbortController | null = null;

  $: run(query, docIds);

  // Toggled off, new search unmounting the panel, or navigating away — stop the stream.
  onDestroy(() => controller?.abort());

  async function run(q: string, ids: string[]) {
    controller?.abort();
    text = ''; sources = []; errMsg = ''; cached = false;
    if (!q.trim() || ids.length === 0) { state = 'idle'; return; }

    const ctrl = new AbortController();
    controller = ctrl;
    state = 'loading';
    try {
      await streamSearchOverview(indexId, q, ids, (e: OverviewEvent) => {
        if (ctrl.signal.aborted) return;
        if (e.type === 'skipped') state = 'skipped';
        else if (e.type === 'sources') sources = e.sources;
        else if (e.type === 'text') { text += e.content; state = 'streaming'; }
        else if (e.type === 'done') { cached = e.cached; state = 'done'; }
        else if (e.type === 'error') { errMsg = e.content; state = 'error'; }
      }, ctrl.signal);
      if (!ctrl.signal.aborted && (state === 'loading' || state === 'streaming')) state = 'done';
    } catch (e) {
      if (ctrl.signal.aborted) return;
      errMsg = e instanceof Error ? e.message : String(e);
      state = 'error';
    }
  }

  /** Split "foo [1] bar [2]" into text and citation pieces, rendered as text (never HTML). */
  function segments(s: string): Array<{ cite?: Source; text: string }> {
    return s.split(/(\[\d+\])/).filter(Boolean).map(part => {
      const m = part.match(/^\[(\d+)\]$/);
      const cite = m ? sources.find(src => src.n === Number(m[1])) : undefined;
      return { cite, text: part };
    });
  }

  function scrollTo(src: Source) {
    const el = document.getElementById(`result-${src.id}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('cited');
      setTimeout(() => el.classList.remove('cited'), 1200);
    } else if (src.url) {
      window.open(src.url, '_blank', 'noopener');
    }
  }
</script>

{#if state !== 'idle' && state !== 'skipped'}
  <div class="overview" aria-live="polite">
    <div class="head">
      <span class="badge">✦ AI Overview</span>
      {#if cached}<span class="meta">cached</span>{/if}
    </div>

    {#if state === 'loading'}
      <div class="shimmer"></div>
      <div class="shimmer short"></div>
    {:else if state === 'error'}
      <p class="err">{errMsg}</p>
    {:else}
      <p class="text">
        {#each segments(text) as seg}
          {#if seg.cite}
            <button class="cite" title={seg.cite.title} on:click={() => seg.cite && scrollTo(seg.cite)}>{seg.cite.n}</button>
          {:else}{seg.text}{/if}
        {/each}{#if state === 'streaming'}<span class="caret">▍</span>{/if}
      </p>
    {/if}

    <p class="disclaimer">AI-generated from the results below — check the sources.</p>
  </div>
{/if}

<style>
  .overview {
    background: var(--bg-3);
    border: 1px solid var(--accent);
    border-radius: 8px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .head { display: flex; align-items: center; gap: 8px; }
  .badge { font-size: 12px; font-weight: 600; color: var(--accent); }
  .meta { font-size: 11px; color: var(--text-6); }
  .text { margin: 0; font-size: 14px; color: var(--text-2); line-height: 1.6; white-space: pre-wrap; }
  .cite {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 18px; height: 18px; padding: 0 4px; margin: 0 2px;
    border: none; border-radius: 9px;
    background: var(--accent); color: #fff;
    font-size: 10px; font-weight: 600; cursor: pointer; vertical-align: text-top;
  }
  .caret { color: var(--accent); animation: blink 1s steps(1) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .shimmer {
    height: 12px; border-radius: 6px;
    background: linear-gradient(90deg, var(--border) 25%, var(--border-2) 50%, var(--border) 75%);
    background-size: 200% 100%;
    animation: shimmer 1.2s linear infinite;
  }
  .shimmer.short { width: 60%; }
  @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
  .err { margin: 0; font-size: 12px; color: var(--danger-text); }
  .disclaimer { margin: 0; font-size: 11px; color: var(--text-6); }
</style>
