import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:8000',
    extraHTTPHeaders: {
      Accept: 'application/json',
    },
  },
});