// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { SitemapConnectorConfig } from '../../lib/types';

export interface PatternRow { value: string }
export interface MetaRow    { key: string; value: string }

export interface SitemapState {
  url:       string;
  maxPages:  number;
  selector:  string;
  userAgent: string;
  pagesPerWorker: number;
  maxWorkers:     number;
}

export function initSitemapFromConnector(connector: Record<string, unknown>): SitemapState {
  return {
    url:       String(connector.url              ?? ''),
    maxPages:  Number(connector.max_pages        ?? 50),
    selector:  String(connector.content_selector ?? ''),
    userAgent: String(connector.user_agent       ?? ''),
    pagesPerWorker: Number(connector.pages_per_worker ?? 1000),
    maxWorkers:     Number(connector.max_workers      ?? 8),
  };
}

export function initIncludePatterns(connector: Record<string, unknown>): PatternRow[] {
  return ((connector.include_patterns ?? []) as string[]).map(v => ({ value: v }));
}

export function initExcludePatterns(connector: Record<string, unknown>): PatternRow[] {
  return ((connector.exclude_patterns ?? []) as string[]).map(v => ({ value: v }));
}

export function initMetaFields(connector: Record<string, unknown>): MetaRow[] {
  return Object.entries((connector.meta_fields ?? {}) as Record<string, string>)
    .map(([k, v]) => ({ key: k, value: v }));
}

export function buildSitemapConfig(
  state:    SitemapState,
  include:  PatternRow[],
  exclude:  PatternRow[],
  meta:     MetaRow[],
): SitemapConnectorConfig {
  return {
    url:              state.url.trim(),
    max_pages:        state.maxPages,
    content_selector: state.selector.trim(),
    user_agent:       state.userAgent.trim(),
    pages_per_worker: state.pagesPerWorker,
    max_workers:      state.maxWorkers,
    include_patterns: include.map(r => r.value.trim()).filter(Boolean),
    exclude_patterns: exclude.map(r => r.value.trim()).filter(Boolean),
    meta_fields: Object.fromEntries(
      meta.filter(r => r.key.trim() && r.value.trim()).map(r => [r.key.trim(), r.value.trim()])
    ),
  };
}
