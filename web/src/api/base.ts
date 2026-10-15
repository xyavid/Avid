/**
 * Where the backend is: the single owner of that fact, so the three transports (REST, SSE, the
 * terminal socket) cannot end up pointing at different hosts. The precedence is settled here and
 * nowhere else — `avid ui` injects a runtime value (it started the backend, so it knows), a page
 * hosted by someone else is built with `VITE_API_BASE`, and with neither it is the page's own origin.
 */

/**
 * Served by the launcher as `/__avid/api-base.js`. `typeof` rather than a truthiness check because
 * the identifier does not exist at all on the other two paths, and that is not an error.
 */
const injected = typeof __AVID_API_BASE__ === 'string' ? __AVID_API_BASE__ : ''

/** Trailing slashes go, so a caller can always paste a path that starts with one. */
export const API_BASE = (injected || import.meta.env.VITE_API_BASE || '').replace(/\/+$/, '')

/** Absolute URL of a backend path, for the places a URL is handed to the browser as a string. */
export function backendUrl(path: string): string {
  return `${API_BASE}${path}`
}

/** The same address over WebSocket; it has to be absolute, so the page's origin resolves the base. */
export function socketUrl(path: string): string {
  const absolute = API_BASE === '' ? new URL(path, window.location.origin).href : backendUrl(path)
  return absolute.replace(/^http/, 'ws')
}
