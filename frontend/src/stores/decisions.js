/**
 * decisions.js — agent decision history.
 *
 * Collection store backed by api.getDecisions(). Each item is the DecisionOut
 * schema (run_id, model, action, confidence, summary, degraded, created_at).
 */
import { createCollectionStore } from './createCollectionStore'
import { api } from '../api'

export const useDecisionsStore = createCollectionStore('decisions', () =>
  api.getDecisions(50)
)
