/**
 * hedges.js — active option hedges.
 *
 * Collection store backed by api.getHedges(). Items are HedgeOut schemas
 * (underlying, occ_symbol, contracts, strike, expiry, premium, status, P&L).
 * The HedgesView renders these as a table plus a simple coverage chart.
 */
import { createCollectionStore } from './createCollectionStore'
import { api } from '../api'

export const useHedgesStore = createCollectionStore('hedges', () =>
  api.getHedges(100)
)
