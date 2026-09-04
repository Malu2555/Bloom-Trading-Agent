<!--
  HedgesView.vue — active option hedges.

  Table of hedges plus a reserved spot for a coverage bar chart. The chart is
  a later pass; the layout and data wiring are already here.
-->
<script setup>
import { onMounted, computed } from 'vue'
import { useHedgesStore } from '@/stores/hedges'
import DataTable from '@/components/DataTable.vue'

const hedges = useHedgesStore()

const columns = [
  { key: 'underlying', label: 'Underlying', sortable: true },
  { key: 'occ_symbol', label: 'OCC' },
  { key: 'contracts', label: 'Contracts', align: 'right' },
  { key: 'strike', label: 'Strike', align: 'right' },
  { key: 'expiry', label: 'Expiry' },
  { key: 'premium_total', label: 'Premium', align: 'right' },
  { key: 'protection_floor', label: 'Floor', align: 'right' },
  { key: 'status', label: 'Status' },
  { key: 'current_pnl', label: 'P&L', align: 'right' },
]

// Placeholder for the coverage chart data (sum of position value per underlying).
const coverageByUnderlying = computed(() => {
  const map = {}
  for (const h of hedges.items) map[h.underlying] = (map[h.underlying] || 0) + 1
  return Object.entries(map).map(([underlying, count]) => ({ underlying, count }))
})

onMounted(() => hedges.fetch())
</script>

<template>
  <section class="flex-col gap-6 p-6">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Hedges</h1>
        <p class="text-sm text-muted">Active option hedges</p>
      </div>
      <button class="btn btn-ghost text-sm" @click="hedges.fetch">Refresh</button>
    </header>

    <!-- chart placeholder: mount a bar chart against coverageByUnderlying -->
    <div class="card">
      <h2 class="text-sm font-semibold mb-2">Coverage by underlying</h2>
      <p class="text-xs text-muted">
        {{ coverageByUnderlying.length }} underlying(s) hedged. Chart coming next.
      </p>
    </div>

    <div class="card">
      <DataTable :columns="columns" :rows="hedges.items" :loading="hedges.loading" />
    </div>
  </section>
</template>
