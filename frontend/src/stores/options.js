/**
 * options.js — open option positions (hedges + speculative).
 *
 * Collection store backed by api.getOptions() (backend returns only open
 * option positions by default). Consider grouping by `underlying` in the view.
 */
import { createCollectionStore } from './createCollectionStore'
import { api } from '../api'

export const useOptionsStore = createCollectionStore('options', () =>
  api.getOptions()
)
