/**
 * vitest 全局准备（`vite.config.ts` 的 `test.setupFiles`）。
 *
 * 目前只补一件事：**`window.matchMedia`**。
 *
 * 为什么需要：`useViewport` 用它做断点判断（`matchMedia` 只在跨阈值时触发，
 * 不必在每次 resize 都跑一遍比较并重渲染），而 jsdom **没有实现**这个 API。
 * 不补的话，任何渲染到 `AppShell` 的用例都会在惰性初始化那一行抛
 * `window.matchMedia is not a function`——报错位置离真正的原因（缺浏览器 API）
 * 很远，容易被误判成断点逻辑写错了。
 *
 * 为什么在全局补而不是各测试文件里补：
 *   1. 这是**环境缺口**，不是某个组件的测试细节——将来任何渲染布局的用例都会撞上，
 *      每个文件各写一遍必然漂移；
 *   2. 它照 jsdom 官方文档给的 `matchMedia` stub 写（"Mocking matchMedia"），
 *      并补上旧版 `addListener`/`removeListener`，让实现不依赖调用方风格。
 *
 * 为什么**直接赋值到 `window` 而不是 `vi.stubGlobal`**：
 * 有用例会调 `vi.unstubAllGlobals()` 收拾自己装的全局（例如 App.test.tsx 装 `fetch`），
 * 那会把这里装的 `matchMedia` 一并拆掉——于是"用例在某些文件里过、在别的文件里挂"。
 * 直接 defineProperty 不会进 vitest 的 stub 登记表，`unstubAllGlobals` 碰不到它。
 *
 * 为什么默认 `matches: false`：jsdom 视口默认 1024x768。此时"< 1180"为真、
 * "≥ 900"为真，正好是"导航列常驻 + 检查器走浮层"的中间档——一个真实的桌面形态，
 * 比默认 true（当成超宽屏）更贴近默认环境的事实。
 *
 * 要测窄屏分支的用例自己传 `matches` 覆盖即可，不必改本文件。
 */

function fakeMatchMedia(query: string): MediaQueryList {
  return {
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    // 旧的 MediaQueryList API：某些第三方代码与老实现仍会调用它。
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  } as unknown as MediaQueryList
}

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: fakeMatchMedia,
  })
}
