/**
 * Typed client for the FastAPI backend (plan Step 11).
 *
 * - Brief routes are Lotto's (`backend/app/briefs/`, PR #36): `POST /briefs`,
 *   `GET /briefs/{id}`, `PATCH /briefs/{id}`.
 * - `POST /calls {brief_id}` is Coco's (PC-6 Start). It may not be deployed yet.
 *
 * Every request sends `Authorization: Bearer <Supabase access token>`. Every
 * mutation (POST, PATCH) also sends `Idempotency-Key`; the caller passes the key
 * so that retrying the same user action reuses it (see `start.ts`).
 * Errors come back in the shared shape `{"error": {"code", "message",
 * "retryable"}}` and are thrown as `ApiError`. Uses `fetch` only.
 */
import type { Brief } from './types'

/** What every brief route returns. `missing_fields` must be empty before Start. */
export interface BriefResponse {
  brief_id: string
  brief: Brief
  missing_fields: string[]
  /** The agent's exact first sentence; null while something is missing. */
  opening_line: string | null
  created_at: string
}

/** `POST /calls` response. Coco's shape isn't fixed yet, so only `call_id` is read, if present. */
export interface StartCallResponse {
  call_id?: string
  /** `dialing`, `creation_uncertain` (still checking) or `failed` (the call was not placed). */
  status?: string
  [key: string]: unknown
}

/** Codes the client makes up itself, next to the server's own codes. */
export const NETWORK_ERROR = 'network_error'
export const NO_SESSION = 'no_session'
export const BAD_RESPONSE = 'bad_response'

/** A failed API call, in the shared error shape plus the HTTP status (0 when no response arrived). */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly retryable: boolean

  constructor(code: string, message: string, status: number, retryable: boolean) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.retryable = retryable
  }
}

export interface ApiConfig {
  /** FastAPI origin, e.g. `https://api.example.com`. No trailing slash needed. */
  baseUrl: string
  /** The signed-in user's access token, or null when signed out. Read before every request. */
  getToken: () => Promise<string | null>
  /** Injected in tests. Defaults to the browser's `fetch`. */
  fetch?: typeof fetch
}

export interface ApiClient {
  createBrief(brief: Brief, idempotencyKey: string): Promise<BriefResponse>
  getBrief(briefId: string): Promise<BriefResponse>
  replaceBrief(briefId: string, brief: Brief, idempotencyKey: string): Promise<BriefResponse>
  startCall(briefId: string, idempotencyKey: string): Promise<StartCallResponse>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Turn a non-2xx response body into an `ApiError`, falling back to a generic message. */
export function parseApiError(status: number, body: unknown): ApiError {
  const error = isRecord(body) ? body.error : undefined
  if (isRecord(error) && typeof error.code === 'string' && typeof error.message === 'string') {
    const retryable = typeof error.retryable === 'boolean' ? error.retryable : status >= 500
    return new ApiError(error.code, error.message, status, retryable)
  }
  return new ApiError(`http_${status}`, `The server answered with an error (HTTP ${status}).`, status, status >= 500)
}

/** Check a brief route's response at the boundary, so the UI never reads a malformed one. */
export function parseBriefResponse(body: unknown): BriefResponse {
  const ok =
    isRecord(body) &&
    typeof body.brief_id === 'string' &&
    isRecord(body.brief) &&
    Array.isArray(body.missing_fields) &&
    body.missing_fields.every((f) => typeof f === 'string') &&
    (body.opening_line === null || typeof body.opening_line === 'string') &&
    typeof body.created_at === 'string'
  if (!ok) throw new ApiError(BAD_RESPONSE, 'The server sent an answer I could not read.', 200, false)
  return body as unknown as BriefResponse
}

/** Strip trailing slashes; empty or blank means "no API configured". */
export function normaliseBaseUrl(raw: string | undefined): string | null {
  const trimmed = (raw ?? '').trim().replace(/\/+$/, '')
  return trimmed === '' ? null : trimmed
}

export function createApiClient(config: ApiConfig): ApiClient {
  const baseUrl = normaliseBaseUrl(config.baseUrl)
  if (!baseUrl) throw new Error('createApiClient needs a baseUrl')
  const doFetch = config.fetch ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init))

  async function request(method: 'GET' | 'POST' | 'PATCH', path: string, body?: unknown, idempotencyKey?: string) {
    const token = await config.getToken()
    if (!token) throw new ApiError(NO_SESSION, "You're signed out. Sign in, then try again.", 401, false)
    const headers: Record<string, string> = { Authorization: `Bearer ${token}`, Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey

    let response: Response
    try {
      response = await doFetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw new ApiError(NETWORK_ERROR, "I couldn't reach the server. Check your connection and try again.", 0, true)
    }
    const json: unknown = await response.json().catch(() => null)
    if (!response.ok) throw parseApiError(response.status, json)
    return json
  }

  return {
    async createBrief(brief, idempotencyKey) {
      return parseBriefResponse(await request('POST', '/briefs', brief, idempotencyKey))
    },
    async getBrief(briefId) {
      return parseBriefResponse(await request('GET', `/briefs/${encodeURIComponent(briefId)}`))
    },
    async replaceBrief(briefId, brief, idempotencyKey) {
      return parseBriefResponse(
        await request('PATCH', `/briefs/${encodeURIComponent(briefId)}`, brief, idempotencyKey),
      )
    },
    async startCall(briefId, idempotencyKey) {
      const json = await request('POST', '/calls', { brief_id: briefId }, idempotencyKey)
      return isRecord(json) ? (json as StartCallResponse) : {}
    },
  }
}
