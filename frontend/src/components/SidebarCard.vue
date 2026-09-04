<!--
  SidebarNav.vue — persistent left navigation for the dashboard.

  Layout uses the utility classes from ../assets/main.css / utilities.css.
  Nav items map to the routes in ../router/index.js. Highlighting the active
  link is done via useRoute() + RouterLink.
-->
<script setup>
import { useRoute } from 'vue-router'
import { api } from '@/api'

const route = useRoute()

// Read-heavy nav links (the 70% dashboard). Extend/order as pages land.
const links = [
  { to: '/', label: 'Dashboard' },
  { to: '/positions', label: 'Positions' },
  { to: '/options', label: 'Options' },
  { to: '/trades', label: 'Trades' },
  { to: '/decisions', label: 'Decisions' },
  { to: '/hedges', label: 'Hedges' },
  { to: '/jobs', label: 'Jobs' },
  { to: '/controls', label: 'Controls', danger: true },
]

function isActive(to) {
  return route.path === to
}

// Action shortcut surfaced in the sidebar (prototype convenience). Wire a
// confirm() prompt + refresh of stores in a later pass.
async function onRefresh() {
  try {
    await api.refresh()
    window.dispatchEvent(new CustomEvent('app:refresh'))
  } catch (err) {
    console.error('refresh failed', err)
  }
}
</script>

<template>
  <aside class="flex-col p-4 gap-4 bg-surface border-r min-h-screen w-56">
    <div class="flex items-center gap-2 px-2">
      <span class="flex items-center justify-center rounded-md bg-primary text-xs font-bold text-white h-7 w-7">AI</span>
      <span class="font-semibold text-sm">Trade Agent</span>
    </div>

    <nav class="flex flex-col gap-1 mt-4 text-sm">
      <RouterLink
        v-for="link in links"
        :key="link.to"
        :to="link.to"
        class="px-2 py-2 rounded-md"
        :class="[
          isActive(link.to)
            ? (link.danger ? 'bg-danger text-white' : 'bg-info text-white')
            : (link.danger ? 'text-danger' : 'text-muted hover-bg-surface'),
        ]"
      >
        {{ link.label }}
      </RouterLink>
    </nav>

    <button class="btn btn-ghost justify-start w-full mt-auto text-xs" @click="onRefresh">
      ↻ Refresh from broker
    </button>
  </aside>
</template>
