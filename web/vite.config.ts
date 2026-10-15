import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Two processes in development: `pnpm dev` (this file) and `uv run avid web`. They deliberately
// stay on different origins, the same shape a deployment has, so the cross-origin path is what
// gets exercised while working; `web/.env.development` says where the backend is.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Small assets stay in their own files: the size gate counts bytes, and inlining would hide
    // them from it until the config changes.
    assetsInlineLimit: 0,
  },
  test: {
    // node by default (pure function tests are fastest); files needing a DOM opt in with
    // `// @vitest-environment jsdom` so the whole suite does not carry jsdom's startup cost.
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // Fills a gap in jsdom (currently matchMedia) globally because it is an environment gap any
    // test that renders layout hits, not a detail of one component's test.
    setupFiles: ['./src/test/setup.ts'],
  },
})
