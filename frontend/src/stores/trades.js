/**
 * trades.js — order history table.
 *
 * Collection store backed by api.getTrades(). Pass a limit when needed:
 * useTradesStore(...).fetch() fetches the default; for paginated/larger loads
 * call fetchTrades(limit) added below.
 */
import { createCollectionStore } from './createCollectionStore'
import { api } from '../api'

export const useTradesStore = createCollectionStore('trades', () =>
  api.getTrades(100)
)

export async function fetchTrades(limit = 100) {
  return api.getTrades(limit)
}
