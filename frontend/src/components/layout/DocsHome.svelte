<script lang="ts">
  // DocsHome — documentation landing panel shown in the main area when the
  // user navigates to the dashboard with no index selected (e.g. after clicking
  // the logo). Reads no store; all state is purely presentational.
  // Links open the static /docs/*.html pages served from public/.

  /** Metadata for each documentation card shown in the grid. */
  const cards: { icon: string; title: string; desc: string; href: string }[] = [
    {
      icon: '⬡',
      title: 'Overview',
      desc: 'What ISP does, the tech stack, service ports, and core user flows.',
      href: '/docs/overview.html',
    },
    {
      icon: '⇄',
      title: 'Connectors',
      desc: 'REST, Sitemap, and Google Sheets connectors — config fields, field mapping, and auth.',
      href: '/docs/connectors.html',
    },
    {
      icon: '◈',
      title: 'Architecture',
      desc: 'Backend 5-layer and frontend 7-layer data architecture with flow diagrams.',
      href: '/docs/architecture.html',
    },
    {
      icon: '⌘',
      title: 'API Reference',
      desc: 'All REST endpoints — indexes, ingest, search, extensions, and the public PLP catalog API.',
      href: '/docs/api.html',
    },
    {
      icon: '⚙',
      title: 'Setup',
      desc: 'Docker Compose setup, environment variables, Google Sheets service account, and Vault.',
      href: '/docs/setup.html',
    },
    {
      icon: '🔒',
      title: 'Security & Secrets',
      desc: 'How secrets.py stores and retrieves keys across env / Vault / AWS, and the available auth approaches.',
      href: '/docs/security.html',
    },
  ];

  /** Quick-start steps shown above the card grid. */
  const steps: { num: string; label: string; detail: string }[] = [
    {
      num: '1',
      label: 'Add a source',
      detail: 'Click the + button in the sidebar to open the Create Wizard.',
    },
    {
      num: '2',
      label: 'Run rebuild',
      detail: 'Open the source and click ↺ Rebuild to index all content.',
    },
    {
      num: '3',
      label: 'Search',
      detail: 'Use the Search tab to run hybrid or lexical queries over your indexed content.',
    },
  ];
</script>

<div class="docs-home">
  <!-- Page heading -->
  <div class="heading">
    <h1>Documentation</h1>
    <p class="subtitle">Everything you need to know about ISP</p>
  </div>

  <!-- Quick-start section -->
  <section class="quickstart">
    <div class="section-label">Quick Start</div>
    <div class="steps">
      {#each steps as step}
        <div class="step">
          <div class="step-num">{step.num}</div>
          <div class="step-body">
            <div class="step-label">{step.label}</div>
            <div class="step-detail">{step.detail}</div>
          </div>
        </div>
      {/each}
    </div>
  </section>

  <!-- Documentation cards grid -->
  <section class="cards-section">
    <div class="section-label">Reference</div>
    <div class="cards">
      {#each cards as card}
        <!-- Open docs pages in the same tab — they are lightweight static HTML -->
        <a class="card" href={card.href} target="_blank" rel="noopener">
          <div class="card-icon">{card.icon}</div>
          <div class="card-title">{card.title}</div>
          <div class="card-desc">{card.desc}</div>
          <div class="card-arrow">Read &rarr;</div>
        </a>
      {/each}
    </div>
  </section>
</div>

<style>
  .docs-home {
    padding: 48px 48px 80px;
    max-width: 860px;
    /* Centre the content in the main panel */
    margin: 0 auto;
  }

  /* ── Heading ────────────────────────────────────────────────────── */
  .heading {
    margin-bottom: 40px;
  }
  h1 {
    font-size: 26px;
    font-weight: 700;
    color: #fff;
    margin: 0 0 6px;
  }
  .subtitle {
    font-size: 14px;
    color: var(--text-5);
    margin: 0;
  }

  /* ── Section label ──────────────────────────────────────────────── */
  .section-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-6);
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 16px;
  }

  /* ── Quick-start steps ──────────────────────────────────────────── */
  .quickstart {
    margin-bottom: 44px;
  }
  .steps {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .step {
    display: flex;
    align-items: flex-start;
    gap: 16px;
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 18px;
  }
  .step-num {
    width: 26px;
    height: 26px;
    border-radius: 50%;
    background: var(--accent-dim);
    border: 1px solid var(--accent-border);
    color: var(--accent);
    font-size: 12px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-top: 1px;
  }
  .step-body {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .step-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-2);
  }
  .step-detail {
    font-size: 13px;
    color: var(--text-5);
    line-height: 1.5;
  }

  /* ── Cards grid ─────────────────────────────────────────────────── */
  .cards-section {
    /* No extra margin; sits below quick-start */
  }
  .cards {
    display: grid;
    /* Two columns on wide viewports, one column on narrow */
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 14px;
  }
  .card {
    background: var(--bg-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 22px;
    text-decoration: none;
    color: inherit;
    display: flex;
    flex-direction: column;
    transition: border-color 0.15s, background 0.15s;
  }
  .card:hover {
    border-color: var(--accent);
    background: var(--accent-dim);
  }
  .card-icon {
    font-size: 20px;
    margin-bottom: 10px;
    color: var(--accent);
  }
  .card-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 6px;
  }
  .card-desc {
    font-size: 13px;
    color: var(--text-5);
    line-height: 1.55;
    flex: 1;
  }
  .card-arrow {
    margin-top: 14px;
    font-size: 12px;
    color: var(--accent);
  }

  /* ── Responsive ─────────────────────────────────────────────────── */
  @media (max-width: 640px) {
    .docs-home {
      padding: 32px 20px 60px;
    }
  }
</style>
