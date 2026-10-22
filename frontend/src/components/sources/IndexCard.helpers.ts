// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

export function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}
