/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string
  readonly VITE_SUPABASE_ANON_KEY: string
  /** "real" to follow a real call on the live-call page; anything else plays the fake call. */
  readonly VITE_LIVE_CALL_SOURCE?: string
  /** The backend's base URL, e.g. http://localhost:8000. */
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
