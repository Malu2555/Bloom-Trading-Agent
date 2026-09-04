/**
 * main.js — application entry point.
 *
 * Order of CSS imports matters:
 *   1. main.css      — design tokens + reset + base components
 *   2. utilities.css — atomic utility classes (can override base)
 *
 * Defaults the theme to dark (trading convention). Toggle by setting
 * document.documentElement.dataset.theme = 'light' | 'dark'.
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { api } from './api'

// --- design system ---------------------------------------------------------
import './assets/main.css'
import './assets/utilities.css'

// dark is the default trading-app theme
document.documentElement.dataset.theme = 'dark'

// --- silent auth -----------------------------------------------------------
// Lightweight cookie auth: bind this browser's HttpOnly session cookie to the
// default Alpaca sandbox profile. No login form, no heavy auth logic. Fire and
// forget (non-blocking); stores that hit 401 simply show empty/error states
// until the bootstrap completes on the first request.
api.silentAuth().catch((err) => {
  console.debug('[auth] silent bootstrap failed:', err && err.message)
})

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')

