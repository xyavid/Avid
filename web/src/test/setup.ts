/**
 * vitest global setup (`test.setupFiles` in `vite.config.ts`): jsdom lacks `matchMedia`, which
 * `state/useViewport` calls while rendering, so it must be installed before any layout test runs.
 * The fake evaluates `min-width`/`max-width` against `window.innerWidth` — a constant
 * `matches: false` would silently push every test into the narrow breakpoint.
 */

/** Parses only `min-width` / `max-width`, the only media queries this codebase uses. */
function evaluate(query: string, width: number): boolean {
  const min = /min-width:\s*(\d+)px/.exec(query)
  const max = /max-width:\s*(\d+)px/.exec(query)
  if (min && width < Number(min[1])) return false
  if (max && width > Number(max[1])) return false
  return true
}

/** Returns a fresh object per call (browser semantics); registered for resize notification. */
const registered: Array<{ query: string; notify: () => void }> = []

function createMediaQueryList(query: string): MediaQueryList {
  const listeners = new Set<() => void>()
  const notify = () => listeners.forEach((cb) => cb())
  registered.push({ query, notify })

  const list = {
    media: query,
    onchange: null,
    // Getter, not a snapshot: evaluates against the current width on each read.
    get matches(): boolean {
      return evaluate(query, window.innerWidth)
    },
    addEventListener: (_type: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_type: string, cb: () => void) => listeners.delete(cb),
    // Legacy MediaQueryList API, still called by older implementations.
    addListener: (cb: () => void) => listeners.add(cb),
    removeListener: (cb: () => void) => listeners.delete(cb),
    dispatchEvent: () => false,
  }
  return list as unknown as MediaQueryList
}

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  // defineProperty, not vi.stubGlobal: vi.unstubAllGlobals() in test files would remove it.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: createMediaQueryList,
  })

  // Tests that change innerWidth must dispatch resize; queries then re-notify their subscribers.
  window.addEventListener('resize', () => {
    for (const item of registered) item.notify()
  })
}
