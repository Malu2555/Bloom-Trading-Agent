/**
 * api.js — CLI REST client (Node side).
 *
 * Same contract as the dashboard's src/api.js, but tailored for Node: reads
 * API_URL from .env (via dotenv), imports nothing browser-only, and uses the
 * native fetch (available in Node >= 18).
 */
import 'dotenv/config'

const BASE_URL = process.env.API_URL || 'http://localhost:8000/api'
const TIMEOUT_MS = (Number(process.env.API_TIMEOUT) || 30) * 1000

/** Simplified response error with the backend's `detail` message. */
export class ApiError extends Error {
  constructor(status, message) {
    super(message || `HTTP ${status}`)
    this.status = status
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)

  let res
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: body != null ? { 'Content-Type': 'application/json' } : undefined,
      body: body != null ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
  } finally {
    clearTimeout(timer)
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const json = await res.json()
      detail = json.detail || detail
    } catch {
      /* keep status-based message */
    }
    throw new ApiError(res.status, detail)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  /* read */
  getPortfolio: () => request('/portfolio'),
  getPositions: () => request('/positions'),
  getOptions:   () => request('/options'),
  getTrades:    (limit = 100) => request(`/trades?limit=${limit}`),
  getDecisions: (limit = 50)  => request(`/decisions?limit=${limit}`),
  getHedges:    (limit = 100) => request(`/hedges?limit=${limit}`),
  getJobs:      (limit = 50)  => request(`/jobs?limit=${limit}`),
  getJob:       (id)          => request(`/jobs/${id}`),

  /* action */
  refresh: () => request('/refresh', { method: 'POST' }),
  runAgent: (body = {}) => request('/agent/cycle', { method: 'POST', body }),

  emergencyFlat:   () => request('/emergency/flat', { method: 'POST', body: { confirm: true } }),
  emergencyClose:  (symbol) => request('/emergency/close', { method: 'POST', body: { symbol } }),
  emergencyHedge:  (payload) => request('/emergency/hedge', { method: 'POST', body: payload }),
  emergencyCancel: () => request('/emergency/cancel-all', { method: 'POST' }),
}

export default api
