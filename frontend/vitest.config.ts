// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from 'vitest/config';
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte({ preprocess: vitePreprocess() })],
  // Resolve Svelte's browser build so lifecycle hooks (onMount/onDestroy)
  // actually run in component tests; the default server build skips them.
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/**/*.{test,spec}.ts'],
    setupFiles: ['./tests/setup.ts'],
  },
});
