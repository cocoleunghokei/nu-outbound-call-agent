/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string
  readonly VITE_SUPABASE_ANON_KEY: string
  /** FastAPI origin, e.g. http://localhost:8000. Empty means demo mode (fake data, nothing sent). */
  readonly VITE_API_URL?: string
  /** "real" to follow a real call on the live-call page; anything else plays the fake call. */
  readonly VITE_LIVE_CALL_SOURCE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
