/**
 * Where the backend is: the single owner of that fact, so the three transports (REST, SSE, the
 * terminal socket) cannot end up pointing at different hosts. Empty means the page's own origin —
 * the shape that needs no configuration — and `VITE_API_BASE` names another origin when the page
 * is hosted by something other than Avid itself.
 */

/** Trailing slashes go, so a caller can always paste a path that starts with one. */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')

/** Absolute URL of a backend path, for the places a URL is handed to the browser as a string. */
export function backendUrl(path: string): string {
  return `${API_BASE}${path}`
}

/** The same address over WebSocket; it has to be absolute, so the page's origin resolves the base. */
export function socketUrl(path: string): string {
  const absolute = API_BASE === '' ? new URL(path, window.location.origin).href : backendUrl(path)
  return absolute.replace(/^http/, 'ws')
}
