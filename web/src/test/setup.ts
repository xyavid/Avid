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
 * **为什么返回一个真正按查询求值的实现，而不是永远 `matches: false`**：
 * 后者看着"够用"，实际会让所有用例都跑在窄屏档下——`useViewport` 判定
 * `isRoomy = false`，于是 `AppShell` 根本不渲染会话导航列（降级成抽屉）。
 * 结果是"想验左栏内容"的用例永远查不到节点，失败信息只说"找不到元素"，
 * 不指向真正的原因。恒 false 还会掩盖另一个事实：桌面端才是本次的验收面。
 *
 * 所以这里解析查询里的 `min-width` / `max-width`（`useViewport` 用的两条都是
 * min-width）并对 `window.innerWidth` 求值。jsdom 默认宽 1024px，默认档于是是
 * "导航列常驻 + 检查器走浮层"——与本次"桌面端"的验收面一致。
 * 要测窄屏分支的用例改 `window.innerWidth` 再 dispatch 一次 `resize` 即可。
 *
 * 写在全局而非各测试文件：它是**环境缺口**，任何渲染布局的用例都会撞上，
 * 各文件自己写必然漂移。
 *
 * 直接 `defineProperty` 到 `window`、不用 `vi.stubGlobal`：后者会被
 * `vi.unstubAllGlobals()`（用例常常要调）一并拆掉，于是变成"在某些文件里过、
 * 在别的文件里挂"。
 */

/** 只解析 min-width / max-width——本项目的媒体查询只用到这两条。 */
function evaluate(query: string, width: number): boolean {
  const min = /min-width:\s*(\d+)px/.exec(query)
  const max = /max-width:\s*(\d+)px/.exec(query)
  if (min && width < Number(min[1])) return false
  if (max && width > Number(max[1])) return false
  return true
}

/** `matchMedia` 每次调用返回**新**对象（与浏览器语义一致），登记起来供 resize 通知。 */
const registered: Array<{ query: string; notify: () => void }> = []

function createMediaQueryList(query: string): MediaQueryList {
  const listeners = new Set<() => void>()
  const notify = () => listeners.forEach((cb) => cb())
  registered.push({ query, notify })

  const list = {
    media: query,
    onchange: null,
    // `matches` 做成 getter：读的时候按**当前**宽度求值，而不是创建时快照。
    get matches(): boolean {
      return evaluate(query, window.innerWidth)
    },
    addEventListener: (_type: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_type: string, cb: () => void) => listeners.delete(cb),
    // 旧的 MediaQueryList API：某些第三方代码与老实现仍会调用它。
    addListener: (cb: () => void) => listeners.add(cb),
    removeListener: (cb: () => void) => listeners.delete(cb),
    dispatchEvent: () => false,
  }
  return list as unknown as MediaQueryList
}

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: createMediaQueryList,
  })

  /*
   * 改完 `window.innerWidth` 后 dispatch 一次 `resize`，所有查询会重新通知订阅者；
   * 组件收到 `change` 后重算断点。没有这一层的话，用例改了宽度但组件收不到通知，
   * 「窄屏下导航消失」这类断言会**静默失效**（因为压根没触发重算，不是因为逻辑对）。
   */
  window.addEventListener('resize', () => {
    for (const item of registered) item.notify()
  })
}
