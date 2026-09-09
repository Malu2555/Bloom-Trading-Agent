/*
  App.vue — root layout.

  Composes the persistent Sidebar with the routed page content. Also listens for
  the 'app:refresh' custom event (fired by Sidebar) — wire each store's fetch()
  into the handler in a later pass so "Refresh from broker" re-pulls all pages.
*/
<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import Sidebar from '@/components/SidebarCard.vue'
import { SpeedInsights } from '@vercel/speed-insights/vue'
import { api } from '@/api'

const authenticated = ref(false)

// Silent auth: provision + bind a profile to this browser's HttpOnly session
// cookie. Must complete before any child component mounts and fetches data
// (Vue mounts children before parents, so DashboardView would otherwise fetch
// before the session cookie exists).
onMounted(async () => {
  try {
    await api.silentAuth()
    const status = await api.whoami()
    authenticated.value = status.authenticated
  } catch (e) {
    console.warn('Silent auth failed:', e?.message || e)
    authenticated.value = false
  }
})

function handleRefresh() {
  // LATER: iterate the loaded Pinia stores and call their fetch().
  window.dispatchEvent(new Event('app:refresh-handled'))
}

onMounted(() => window.addEventListener('app:refresh', handleRefresh))
onUnmounted(() => window.removeEventListener('app:refresh', handleRefresh))
</script>

<template>
  <div class="flex min-h-screen bg-bg">
    <Sidebar />
    <main class="flex-1 overflow-auto">
      <!-- Only mount routes after silent auth has set the session cookie, so the
           dashboard's fetch() calls don't race ahead of /auth/bootstrap. -->
      <RouterView v-if="authenticated" />
    </main>
    <SpeedInsights />
  </div>
</template>
