<!--
  Copyright 2026 Shekhar Maheshwari
  SPDX-License-Identifier: Apache-2.0
-->

<script lang="ts">
  import type { VolumePoint } from '../../lib/types';

  export let volume: VolumePoint[] = [];

  const W = 800;
  const H = 160;
  const PAD = { top: 12, right: 16, bottom: 32, left: 44 };
  const CW = W - PAD.left - PAD.right;
  const CH = H - PAD.top - PAD.bottom;

  $: totalSearches = volume.reduce((s, v) => s + v.count, 0);
  $: totalClicks   = volume.reduce((s, v) => s + v.clicks, 0);
  $: maxY = Math.max(...volume.map(v => Math.max(v.count, v.clicks)), 1);

  function xOf(i: number): number {
    return volume.length < 2 ? PAD.left : PAD.left + (i / (volume.length - 1)) * CW;
  }
  function yOf(val: number): number {
    return PAD.top + CH - (val / maxY) * CH;
  }

  function polyline(getter: (v: VolumePoint) => number): string {
    return volume.map((v, i) => `${xOf(i)},${yOf(getter(v))}`).join(' ');
  }
  function area(getter: (v: VolumePoint) => number): string {
    if (volume.length === 0) return '';
    const pts = volume.map((v, i) => `${xOf(i)},${yOf(getter(v))}`).join(' ');
    const base = `${xOf(volume.length - 1)},${PAD.top + CH} ${xOf(0)},${PAD.top + CH}`;
    return `${pts} ${base}`;
  }

  // Y-axis ticks — 4 evenly spaced
  $: yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => ({
    val: Math.round(maxY * f),
    y:   yOf(maxY * f),
  }));

  // X-axis labels — show ~6 evenly spaced dates
  $: xLabels = (() => {
    if (volume.length === 0) return [];
    const step = Math.max(1, Math.floor(volume.length / 6));
    return volume
      .map((v, i) => ({ i, label: fmt(v.date) }))
      .filter((_, i) => i % step === 0 || i === volume.length - 1);
  })();

  // Tooltip state
  let hoverIdx: number | null = null;
  let tooltipX = 0;
  let tooltipY = 0;

  function onMouseMove(e: MouseEvent) {
    const svg = (e.currentTarget as SVGElement).getBoundingClientRect();
    const mx = (e.clientX - svg.left) * (W / svg.width) - PAD.left;
    const idx = Math.round((mx / CW) * (volume.length - 1));
    hoverIdx = Math.max(0, Math.min(volume.length - 1, idx));
    tooltipX = xOf(hoverIdx);
    tooltipY = Math.min(yOf(volume[hoverIdx].count), yOf(volume[hoverIdx].clicks)) - 8;
  }
  function onMouseLeave() { hoverIdx = null; }

  function fmt(iso: string): string {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }
  function num(n: number): string { return n.toLocaleString(); }
</script>

<div class="card">
  <div class="card-header">
    <span class="card-title">Searches &amp; Clicks</span>
    <div class="legend">
      <span class="leg-dot searches"></span><span class="leg-label">Searches {num(totalSearches)}</span>
      <span class="leg-dot clicks"></span><span class="leg-label">Clicks {num(totalClicks)}</span>
    </div>
  </div>

  {#if volume.length === 0}
    <div class="empty">No data yet</div>
  {:else}
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <svg
      viewBox="0 0 {W} {H}"
      class="chart"
      on:mousemove={onMouseMove}
      on:mouseleave={onMouseLeave}
    >
      <defs>
        <linearGradient id="grad-search" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.18"/>
          <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
        </linearGradient>
        <linearGradient id="grad-click" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#22c55e" stop-opacity="0.15"/>
          <stop offset="100%" stop-color="#22c55e" stop-opacity="0"/>
        </linearGradient>
      </defs>

      <!-- Y-axis gridlines + labels -->
      {#each yTicks as t}
        <line x1={PAD.left} y1={t.y} x2={W - PAD.right} y2={t.y}
              stroke="var(--border)" stroke-width="1" stroke-dasharray="3 3" />
        <text x={PAD.left - 6} y={t.y + 4} class="axis-label" text-anchor="end">{num(t.val)}</text>
      {/each}

      <!-- X-axis labels -->
      {#each xLabels as xl}
        <text x={xOf(xl.i)} y={H - 4} class="axis-label" text-anchor="middle">{xl.label}</text>
      {/each}

      <!-- Area fills -->
      <polygon points={area(v => v.count)}  fill="url(#grad-search)" />
      <polygon points={area(v => v.clicks)} fill="url(#grad-click)"  />

      <!-- Lines -->
      <polyline points={polyline(v => v.count)}  class="line searches" />
      <polyline points={polyline(v => v.clicks)} class="line clicks"   />

      <!-- Hover crosshair + dots -->
      {#if hoverIdx !== null}
        {@const pt = volume[hoverIdx]}
        <line x1={xOf(hoverIdx)} y1={PAD.top} x2={xOf(hoverIdx)} y2={PAD.top + CH}
              stroke="var(--border-3)" stroke-width="1" stroke-dasharray="4 2" />
        <circle cx={xOf(hoverIdx)} cy={yOf(pt.count)}  r="4" class="dot searches" />
        <circle cx={xOf(hoverIdx)} cy={yOf(pt.clicks)} r="4" class="dot clicks"   />

        <!-- Tooltip -->
        <g transform="translate({tooltipX}, {tooltipY})">
          <rect x="-64" y="-48" width="128" height="46" rx="6"
                fill="var(--bg-3)" stroke="var(--border-2)" stroke-width="1" />
          <text x="0" y="-31" class="tip-date" text-anchor="middle">{fmt(pt.date)}</text>
          <text x="-44" y="-14" class="tip-searches">⬤ {num(pt.count)} searches</text>
          <text x="-44" y="0"   class="tip-clicks">⬤ {num(pt.clicks)} clicks</text>
        </g>
      {/if}
    </svg>
  {/if}
</div>

<style>
  .card        { background:var(--bg-2); border:1px solid var(--border); border-radius:10px; padding:20px; }
  .card-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; flex-wrap:wrap; gap:8px; }
  .card-title  { font-size:13px; font-weight:600; color:var(--text); }
  .empty       { font-size:12px; color:var(--text-6); text-align:center; padding:40px 0; }

  .legend      { display:flex; align-items:center; gap:12px; }
  .leg-dot     { width:10px; height:10px; border-radius:50%; display:inline-block; }
  .leg-dot.searches { background:var(--accent); }
  .leg-dot.clicks   { background:#22c55e; }
  .leg-label   { font-size:11px; color:var(--text-5); }

  .chart       { width:100%; display:block; }

  .axis-label  { font-size:10px; fill:var(--text-7); font-family:inherit; }

  .line        { fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
  .line.searches { stroke:var(--accent); }
  .line.clicks   { stroke:#22c55e; }

  .dot         { stroke-width:2; }
  .dot.searches { fill:var(--accent); stroke:var(--bg-2); }
  .dot.clicks   { fill:#22c55e;      stroke:var(--bg-2); }

  .tip-date     { font-size:10px; fill:var(--text-5); font-family:inherit; font-weight:600; }
  .tip-searches { font-size:10px; fill:var(--accent); font-family:inherit; }
  .tip-clicks   { font-size:10px; fill:#22c55e;      font-family:inherit; }
</style>
