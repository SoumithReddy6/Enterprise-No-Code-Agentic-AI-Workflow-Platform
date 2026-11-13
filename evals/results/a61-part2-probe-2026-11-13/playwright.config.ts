import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: '.', workers: 1, retries: 0, timeout: 90_000, reporter: [['list']],
  outputDir: './out',
  use: { baseURL: 'http://127.0.0.1:3317', browserName: 'chromium' },
  webServer: { command: 'node node_modules/vite/bin/vite.js --config tests/browser/vite.config.ts', cwd: '/Users/soumith/Library/Mobile Documents/iCloud~md~obsidian/Documents/Soumith/No Code Agentic AI/frontend', url: 'http://127.0.0.1:3317', reuseExistingServer: false, timeout: 120_000 },
});
