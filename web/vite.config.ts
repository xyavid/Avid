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
        // SSE 不能被代理缓冲：`ws: false` 是不让它走 WebSocket 升级（走普通 HTTP 流），
        // 后端再配合禁用中间层压缩，增量才会逐帧到达。
        ws: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 小资源也一律走独立文件：体积门禁（阶段 32 已删）曾据此统计字节，新的体积门禁
    // 定下来之前保留这条，避免静态资源悄悄内联进 JS。
    assetsInlineLimit: 0,
  },
  test: {
    // 默认 node（纯函数用例跑得最快）；需要 DOM 的用例在文件头用
    // `// @vitest-environment jsdom` 单独切换，不必让全部用例都背 jsdom 的启动成本。
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
