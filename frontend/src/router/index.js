/**
 * index.js — application routes.
 *
 * The dashboard's read-heavy pages. Each view is lazily loaded so the initial
 * bundle stays small. Add a catch-all 404 and auth guards in a later pass.
 */
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
    },
    {
      path: '/positions',
      name: 'positions',
      component: () => import('@/views/PositionsView.vue'),
    },
    {
      path: '/options',
      name: 'options',
      component: () => import('@/views/OptionsView.vue'),
    },
    {
      path: '/trades',
      name: 'trades',
      component: () => import('@/views/TradesView.vue'),
    },
    {
      path: '/decisions',
      name: 'decisions',
      component: () => import('@/views/DecisionsView.vue'),
    },
    {
      path: '/hedges',
      name: 'hedges',
      component: () => import('@/views/HedgesView.vue'),
    },
    {
      path: '/jobs',
      name: 'jobs',
      component: () => import('@/views/JobsView.vue'),
    },
    {
      path: '/controls',
      name: 'controls',
      component: () => import('@/views/EmergencyView.vue'),
    },
  ],
})

export default router

