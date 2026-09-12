import { test, expect } from '@playwright/test'

/**
 * Frontend → backend integration test.
 *
 * The Vite dev server (Playwright's `webServer`, see playwright.config.js) serves
 * the Vue app and proxies `/api` to the Django backend on :8000 (see vite.config.js).
 * This spec verifies the whole chain actually works against a LIVE backend:
 *
 *   1. the app loads from the frontend origin,
 *   2. `POST /api/auth/bootstrap` goes through the Vite proxy and binds an
 *      HttpOnly session cookie to a real UserProfile on Django,
 *   3. `App.vue` confirms via `GET /api/auth/whoami` and only then mounts the
 *      Dashboard — so seeing the Dashboard proves the session was established.
 *
 * Requires the Django backend to be running on :8000, e.g.:
 *   cd backend && python manage.py runserver
 */
test('frontend silently authenticates against the live backend and renders the dashboard', async ({
  page,
}) => {
  // Round-trip through the frontend's own Vite proxy. A fresh browser session has
  // no cookie yet, so whoami reports anonymous before the app boots auth.
  const before = await (await page.request.get('/api/auth/whoami')).json()
  expect(before).toHaveProperty('authenticated', false)

  // Load the app. Silent auth boots on mount; the Dashboard is only mounted after
  // bootstrap + whoami succeed, i.e. the backend is genuinely reachable.
  await page.goto('/')
  await expect(
    page.getByRole('heading', { name: 'Dashboard', level: 1 }),
  ).toBeVisible({ timeout: 20000 })

  // page.request shares the browser context's cookie store, so whoami must now
  // reflect the session the bootstrap just bound to the backend.
  const authed = await (await page.request.get('/api/auth/whoami')).json()
  expect(authed).toHaveProperty('authenticated', true)
})

