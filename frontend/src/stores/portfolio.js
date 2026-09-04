/**
 * portfolio.js — single portfolio snapshot store (not a list).
 *
 * Unlike the collection stores, the dashboard only ever shows the latest
 * PortfolioSnapshot, so this store holds a single object keyed off
 * api.getPortfolio(). Extend with computeds (e.g. formatted P&L) as needed.
 */
import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api'

export const usePortfolioStore = defineStore('portfolio', () => {
  const snapshot = ref(null)   // PortfolioOut | null
  const loading = ref(false)
  const error = ref(null)

  const equity = computed(() => Number(snapshot.value?.equity ?? 0))
  const pnl = computed(() => Number(snapshot.value?.unrealized_pl ?? 0))
  const dayPnl = computed(() => Number(snapshot.value?.day_pnl ?? 0))

  async function fetch() {
    loading.value = true
    error.value = null
    try {
      snapshot.value = await api.getPortfolio()
    } catch (e) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
    return snapshot.value
  }

  return { snapshot, loading, error, equity, pnl, dayPnl, fetch }
})
