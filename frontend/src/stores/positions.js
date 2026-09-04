/**
 * positions.js — open stock positions table.
 *
 * Collection store backed by api.getPositions(). `items` entries match the
 * backend PositionOut schema. Add computeds here (e.g. total market value,
 * net P&L) as the PositionsView needs them.
 */
import { computed } from 'vue'
import { createCollectionStore } from './createCollectionStore'
import { api } from '../api'

export const usePositionsStore = createCollectionStore('positions', () =>
  api.getPositions()
)

// typed-helper re-export so stores can be extended later if needed
export const usePositionsComputed = (store) => computed(() => store.items)
