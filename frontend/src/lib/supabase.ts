import { createClient, type SupabaseClient } from '@supabase/supabase-js'

/**
 * Reads a required Vite environment variable and fails fast with a clear
 * message when it is missing, instead of letting supabase-js fail later.
 */
function requireEnv(name: 'VITE_SUPABASE_URL' | 'VITE_SUPABASE_ANON_KEY'): string {
  const value = import.meta.env[name]
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${name} is not set. Copy frontend/.env.example to frontend/.env.local and fill it in.`)
  }
  return value.trim()
}

/**
 * Shared Supabase client for the browser.
 *
 * Uses the public anon key only, so every table it reads must have row-level
 * security. Never put the service role key in a VITE_ variable.
 */
export const supabase: SupabaseClient = createClient(
  requireEnv('VITE_SUPABASE_URL'),
  requireEnv('VITE_SUPABASE_ANON_KEY'),
)
