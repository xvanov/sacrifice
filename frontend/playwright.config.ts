import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env['SACRIFICE_API_URL'] ?? 'http://localhost:8000',
    extraHTTPHeaders: {},
  },
});