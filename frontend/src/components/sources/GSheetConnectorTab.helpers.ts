// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { GSheetConnectorConfig } from '../../lib/types';

export interface MappingRow { key: string; value: string }

export interface GSheetState {
  spreadsheetId: string;
  sheetName:     string;
  headerRow:     number;
  idCol:         string;
  titleCol:      string;
  contentCol:    string;
  urlCol:        string;
  skipEmpty:     boolean;
}

export function initGsheetFromConnector(connector: Record<string, unknown>): GSheetState {
  return {
    spreadsheetId: String(connector.spreadsheet_id  ?? ''),
    sheetName:     String(connector.sheet_name      ?? 'Sheet1'),
    headerRow:     Number(connector.header_row      ?? 1),
    idCol:         String(connector.id_column       ?? ''),
    titleCol:      String(connector.title_column    ?? ''),
    contentCol:    String(connector.content_column  ?? ''),
    urlCol:        String(connector.url_column      ?? ''),
    skipEmpty:     Boolean(connector.skip_empty_rows ?? true),
  };
}

export function initGsheetMappings(connector: Record<string, unknown>): MappingRow[] {
  return Object.entries((connector.field_map ?? {}) as Record<string, string>)
    .map(([k, v]) => ({ key: k, value: v }));
}

export function buildGsheetConfig(state: GSheetState, mappings: MappingRow[]): GSheetConnectorConfig {
  return {
    type:            'gsheet',
    spreadsheet_id:  state.spreadsheetId.trim(),
    sheet_name:      state.sheetName.trim() || 'Sheet1',
    header_row:      state.headerRow,
    id_column:       state.idCol.trim(),
    title_column:    state.titleCol.trim(),
    content_column:  state.contentCol.trim(),
    url_column:      state.urlCol.trim(),
    skip_empty_rows: state.skipEmpty,
    field_map: Object.fromEntries(
      mappings
        .filter(r => r.key.trim() && r.value.trim())
        .map(r => [r.key.trim(), r.value.trim()])
    ),
  };
}
