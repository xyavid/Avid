/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute origin of the backend; unset (or empty) means the page's own origin. */
  readonly VITE_API_BASE?: string
}
