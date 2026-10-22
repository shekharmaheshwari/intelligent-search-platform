// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';

export default defineConfig({
  integrations: [svelte()],
  output: 'static',
});
