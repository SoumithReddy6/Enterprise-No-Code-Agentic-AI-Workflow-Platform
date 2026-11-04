import base from '../../vite.config.ts';

const config = {
  ...base,
  cacheDir: '.vite-browser-tests',
  server: {
    ...base.server,
    host: '127.0.0.1',
    port: 3317,
    strictPort: true,
    // No fallback to the real API if a fixture is missing.
    proxy: undefined,
  },
};

export default config;
