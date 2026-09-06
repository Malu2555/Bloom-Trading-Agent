/**
 * api.js — thin REST client for the Django Ninja backend.
 *
 * Uses the native `fetch` API (no axios dependency) so this file works in BOTH
 * the browser (Vue dashboard) and Node.js (the CLI lives in ../cli and reuses
 * the same module). Backend endpoints mirror backend/api/routes.py.
 *
 * Base URL is read from the Vite env var VITE_API_URL (see .env) and falls back
 * to the local dev default.
 *
 * NOTE ON THE PLAN:
 *  - This is a hand-written, structure-first client. If we later generate one
 *    from `GET /api/openapi.json`, keep these method names so stores don't
 *    change.
 */

const IS_BROWSER = typeof window !== 'undefined'

/**
 * Base URL. In the browser we stay same-origin via the Vite dev proxy (/api ->
 * http://localhost:8000) so the HttpOnly session cookie flows without CORS. In
 * Node (the re-usable CLI module) we target the backend directly.
 */
const API_BASE = import.meta.env?.VITE_API_URL
const BASE_URL = API_BASE
  ? `${API_BASE}/api`
  : IS_BROWSER
    ? '/api'
    : 'http://localhost:8000/api'

/** Read the Django CSRF cookie (readable by design; session cookie stays HttpOnly). */
function getCsrfToken() {
  const match = IS_BROWSER ? /(?:^|;\s*)csrftoken=([^;]*)/.exec(document.cookie) : null
  return match ? decodeURIComponent(match[1]) : ''
}

/**
 * Shared fetch wrapper: attaches JSON, error handling, the HttpOnly session
 * cookie (credentials: 'include') and, for mutating calls, the CSRF header.
 * @param {string} path   - e.g. "/portfolio"
 * @param {object} [opts] - fetch options (method, body, ...)
 */
async function request(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...opts.headers }
  const method = (opts.method || 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD' && !headers['X-CSRFToken']) {
    const token = getCsrfToken()
    if (token) headers['X-CSRFToken'] = token
  }
  const res = await fetch(`${BASE_URL}${path}`, { ...opts, headers, credentials: 'include' })

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const json = await res.json()
      detail = json.detail || detail
    } catch {
      /* non-JSON error body — keep the status-based message */
    }
    throw new Error(detail)
  }
  // 204 No Content (and similar) have no body
  return res.status === 204 ? null : res.json()
}

export const api = {
  /* ---------------- silent authentication ---------------- */
  /** Provision + bind a profile to this browser's HttpOnly session cookie. */
  silentAuth: (slug) =>
    request('/auth/bootstrap', {
      method: 'POST',
      body: JSON.stringify(slug ? { slug } : {}),
    }),
  /** Current session's auth status (no secrets). */
  whoami: () => request('/auth/whoami'),
  /** Forget the bound profile (keeps the session cookie alive). */
  logout: () => request('/auth/logout', { method: 'POST' }),

  /* ---------------- read endpoints ---------------- */
  getPortfolio: () => request('/portfolio'),
  getPositions: () => request('/positions'),
  getOptions:   () => request('/options'),
  getTrades:    (limit = 100) => request(`/trades?limit=${limit}`),
  getDecisions: (limit = 50)  => request(`/decisions?limit=${limit}`),
  getHedges:    (limit = 100) => request(`/hedges?limit=${limit}`),
  getJobs:      (limit = 50)  => request(`/jobs?limit=${limit}`),
  getJob:       (jobId)       => request(`/jobs/${jobId}`),

  /* ---------------- action endpoints ---------------- */
  /** Pull live Alpaca state for the authenticated user into tracking tables. */
  refresh: () => request('/refresh', { method: 'POST' }),

  /** Run one agent decision cycle for the authenticated user. */
  runAgent: (body = {}) =>
    request('/agent/cycle', { method: 'POST', body: JSON.stringify(body) }),

  /** Read the authenticated user's effective agent runtime config. */
  agentConfigGet: () => request('/agent/config'),

  /** Persist per-profile agent runtime config (partial update). */
  agentConfigSet: (cfg) =>
    request('/agent/config', { method: 'PATCH', body: JSON.stringify(cfg) }),

  /** Pull the authenticated user's perception into their owned semantic memory. */
  ingestPerception: (symbols = []) =>
    request('/agent/ingest', { method: 'POST', body: JSON.stringify({ symbols }) }),

  /* --- emergency (hardcoded CLI fallback surfaced via REST) --- */
  emergencyFlat:   () => request('/emergency/flat', { method: 'POST', body: JSON.stringify({ confirm: true }) }),
  emergencyClose:  (symbol) => request('/emergency/close', { method: 'POST', body: JSON.stringify({ symbol }) }),
  emergencyHedge:  (payload) => request('/emergency/hedge', { method: 'POST', body: JSON.stringify(payload) }),
  emergencyCancel: () => request('/emergency/cancel-all', { method: 'POST' }),
}

export default api

