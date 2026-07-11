<script lang="ts">
  import { onMount, createEventDispatcher } from 'svelte';
  import { indexService } from '../../lib/services/indexes/IndexService';
  import { settingsService } from '../../lib/services/settings/SettingsService';
  import type { Index, IngestStatus } from '../../lib/services/indexes/index.model';
  import type { EmbedModelOption } from '../../lib/types';
  import { fmtDate } from './IndexDetail.helpers';

  import OverviewTab              from './OverviewTab.svelte';
  import RestConnectorTab         from './RestConnectorTab.svelte';
  import SitemapConnectorTab      from './SitemapConnectorTab.svelte';
  import GSheetConnectorTab       from './GSheetConnectorTab.svelte';
  import DocumentTable            from './DocumentTable.svelte';
  import SearchPanel              from './SearchPanel.svelte';
  import ApiPanel                 from './ApiPanel.svelte';
  import SuggestionsTab           from './SuggestionsTab.svelte';
  import MappingTab               from './MappingTab.svelte';
  import AffinityDimensionsPanel  from './AffinityDimensionsPanel.svelte';

  export let indexId: string;
  export let tab: 'overview' | 'connector' | 'mapping' | 'documents' | 'search' | 'api' | 'suggestions' | 'personalisation' = 'overview';

  const tabs: Array<'overview' | 'connector' | 'mapping' | 'documents' | 'search' | 'api' | 'suggestions' | 'personalisation'> =
    ['overview', 'connector', 'mapping', 'documents', 'search', 'api', 'suggestions', 'personalisation'];
  import BoostRulesTab            from './BoostRulesTab.svelte';

  export let indexId: string;
  export let tab: 'overview' | 'connector' | 'mapping' | 'documents' | 'search' | 'api' | 'suggestions' | 'personalisation' | 'boost-rules' = 'overview';

  const tabs: Array<'overview' | 'connector' | 'mapping' | 'documents' | 'search' | 'api' | 'suggestions' | 'personalisation' | 'boost-rules'> =
    ['overview', 'connector', 'mapping', 'documents', 'search', 'api', 'suggestions', 'personalisation', 'boost-rules'];

  const dispatch = createEventDispatcher<{ back: void }>();

  let meta: Index | null             = null;
  let status: IngestStatus | null    = null;
  let availableModels: EmbedModelOption[] = [];

  $: connector     = meta?.connectors[0] ?? {};
  $: connectorType = (connector.type ?? '') as 'rest' | 'sitemap' | 'gsheet' | '';

  onMount(async () => {
    const [fetchedMeta, fetchedStatus, settings] = await Promise.all([
      indexService.getIndex(indexId),
      indexService.getStatus(indexId),
      settingsService.getSettings(),
    ]);
    meta            = fetchedMeta;
    status          = fetchedStatus;
    availableModels = settings.available_models ?? [];
  });
</script>

<div class="detail">
  <div class="breadcrumb">
    <button on:click={() => dispatch('back')}>← Indexes</button>
    {#if meta}<span>/ {meta.name}</span>{/if}
  </div>

  {#if !meta}
    <div class="loading">Loading…</div>
  {:else}
    <div class="detail-head">
      <div>
        <h1>{meta.name}</h1>
        {#if meta.description}<p class="desc">{meta.description}</p>{/if}
      </div>
      <div class="stats">
        <div class="stat">
          <span class="stat-val">{meta.docCount.toLocaleString()}</span>
          <span class="stat-lbl">Documents</span>
        </div>
        <div class="stat">
          <span class="stat-val">{meta.connectors.length}</span>
          <span class="stat-lbl">Connectors</span>
        </div>
        <div class="stat">
          <span class="stat-val">{fmtDate(meta.createdAt)}</span>
          <span class="stat-lbl">Created</span>
        </div>
      </div>
    </div>

    <div class="tabs">
      {#each tabs as t}
        <button class:active={tab === t} on:click={() => tab = t}>
          {t === 'boost-rules' ? 'Boost Rules' : t.charAt(0).toUpperCase() + t.slice(1)}
        </button>
      {/each}
    </div>

    <div class="tab-body">
      {#if tab === 'overview'}
        <OverviewTab {indexId} bind:meta {status} {availableModels} />

      {:else if tab === 'connector'}
        <div class="connector-tab">
          {#if connectorType === 'rest'}
            <RestConnectorTab {connector} />
          {:else if connectorType === 'sitemap'}
            <SitemapConnectorTab {indexId} {connector} />
          {:else if connectorType === 'gsheet'}
            <GSheetConnectorTab {indexId} {connector} />
          {:else}
            <p class="no-connector">No connector configured for this index.</p>
          {/if}
        </div>

      {:else if tab === 'mapping'}
        <MappingTab
          {indexId}
          {connector}
          intentLabel={meta.intentLabel ?? null}
        />

      {:else if tab === 'documents'}
        <DocumentTable {indexId} />

      {:else if tab === 'search'}
        <SearchPanel {indexId} />

      {:else if tab === 'api'}
        <ApiPanel {indexId} />

      {:else if tab === 'suggestions'}
        <SuggestionsTab {indexId} category={meta.intentLabel ?? undefined} />

      {:else if tab === 'personalisation'}
        <AffinityDimensionsPanel {indexId} />

      {:else if tab === 'boost-rules'}
        <BoostRulesTab {indexId} />
      {/if}
    </div>
  {/if}
</div>

<style>
  .detail { display: flex; flex-direction: column; gap: 0; }

  .breadcrumb {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 20px; font-size: 13px; color: var(--text-4);
  }
  .breadcrumb button {
    background: none; border: none; color: var(--accent); cursor: pointer; font-size: 13px;
  }
  .breadcrumb button:hover { text-decoration: underline; }

  .loading { color: var(--text-5); padding: 40px 0; text-align: center; }

  .detail-head {
    display: flex; justify-content: space-between; align-items: flex-start;
    margin-bottom: 24px; flex-wrap: wrap; gap: 16px;
  }
  h1 { margin: 0 0 4px; font-size: 22px; color: var(--text); }
  .desc { margin: 0; font-size: 13px; color: var(--text-4); }

  .stats { display: flex; gap: 24px; }
  .stat  { display: flex; flex-direction: column; align-items: flex-end; }
  .stat-val { font-size: 18px; font-weight: 600; color: var(--text); }
  .stat-lbl { font-size: 11px; color: var(--text-5); text-transform: uppercase; letter-spacing: 0.5px; }

  .tabs { display: flex; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
  .tabs button {
    background: none; border: none; border-bottom: 2px solid transparent;
    padding: 10px 16px; color: var(--text-4); font-size: 13px; cursor: pointer; margin-bottom: -1px;
  }
  .tabs button.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tabs button:hover:not(.active) { color: var(--text-3); }

  .tab-body { min-height: 200px; }
  .connector-tab { display: flex; flex-direction: column; gap: 20px; }
  .no-connector { color: var(--text-5); font-size: 13px; margin: 0; }
</style>
