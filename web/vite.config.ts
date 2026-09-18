import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 开发期两个进程：`pnpm dev`（本文件）+ `uv run avid web`。
// Vite 把 /api 代理到后端，于是浏览器只看到一个源，SSE 也不需要 CORS。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        // SSE 不能被代理缓冲：关掉压缩是让它逐帧到达的最省事做法。
        ws: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 位图纹理禁止进仓（C17）：小资源也一律走独立文件，便于 gate:size 统计。
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
  test: {
    // 默认 node（纯函数用例跑得最快）；需要 DOM 的组件用例在文件头用
    // `// @vitest-environment jsdom` 单独切换，不必让全部用例都背 jsdom 的启动成本。
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
