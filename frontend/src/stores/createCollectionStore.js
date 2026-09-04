/**
 * createCollectionStore.js — reusable Pinia store for list/table resources.
 *
 * Dashboard pages are read-heavy, so the one pattern we need everywhere is:
 *   items | loading | error | fetch()
 *
 * Instead of copy-pasting that in 6 stores, this factory builds a store given a
 * name and a fetch function. Individual domain stores (positions, trades, ...)
 * call this then extend the returned store if they need computed getters.
 *
 * Uses the setup-store style already used by this scaffold (see counter.js).
 *
 * @param {string}  id          - unique store id, e.g. 'positions'
 * @param {Function} fetcher    - async () => payload, returning an array
 * @returns {Function}          - a useXxxStore() Pinia store composable
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'

export function createCollectionStore(id, fetcher) {
  return defineStore(id, () => {
    const items = ref([])      // resolved list
    const loading = ref(false) // in-flight indicator
    const error = ref(null)    // last fetch error (string) or null
    const lastUpdated = ref(null) // ISO timestamp of last successful fetch

    /** Fetch (or re-fetch) the collection. */
    async function fetch() {
      loading.value = true
      error.value = null
      try {
        const data = await fetcher()
        items.value = Array.isArray(data) ? data : []
        lastUpdated.value = new Date().toISOString()
      } catch (e) {
        error.value = e?.message || String(e)
      } finally {
        loading.value = false
      }
      return items.value
    }

    /** Replace the in-memory list without hitting the network. */
    function setItems(next) {
      items.value = Array.isArray(next) ? next : []
      lastUpdated.value = new Date().toISOString()
    }

    return { items, loading, error, lastUpdated, fetch, setItems }
  })
}
