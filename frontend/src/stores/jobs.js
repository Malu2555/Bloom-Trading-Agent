/**
 * jobs.js — background JobRun statuses.
 *
 * Collection store backed by api.getJobs(). Useful for showing whether a
 * refresh or agent cycle is still running. The JobsView shows status badges
 * (pending / running / success / failed / cancelled).
 */
import { createCollectionStore } from './createCollectionStore'
import { api } from '../api'

export const useJobsStore = createCollectionStore('jobs', () =>
  api.getJobs(50)
)
