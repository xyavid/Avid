/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute origin of the backend; unset (or empty) means the page's own origin. */
  readonly VITE_API_BASE?: string
}

/** Injected by `avid ui` at serve time; absent on every other path. */
declare var __AVID_API_BASE__: string | undefined
