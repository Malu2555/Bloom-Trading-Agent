<!--
  PositionsView.vue — open stock positions table.

  Wires the positions collection store into the generic DataTable. Add a
  row-click detail modal and dollar formatting helpers in a later pass.
-->
<script setup>
import { onMounted } from 'vue'
import { usePositionsStore } from '@/stores/positions'
import DataTable from '@/components/DataTable.vue'

const positions = usePositionsStore()

const columns = [
  { key: 'symbol', label: 'Symbol', sortable: true },
  { key: 'qty', label: 'Qty', align: 'right' },
  { key: 'avg_entry_price', label: 'Avg Entry', align: 'right' },
  { key: 'current_price', label: 'Current', align: 'right' },
  { key: 'market_value', label: 'Market Value', align: 'right' },
  { key: 'unrealized_pl', label: 'P&L', align: 'right' },
  { key: 'unrealized_plpc', label: 'P&L %', align: 'right' },
]

onMounted(() => positions.fetch())
</script>

<template>
  <section class="flex-col gap-6 p-6">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Positions</h1>
        <p class="text-sm text-muted">Open stock positions</p>
      </div>
      <button class="btn btn-ghost text-sm" @click="positions.fetch">Refresh</button>
    </header>

    <div class="card">
      <DataTable :columns="columns" :rows="positions.items" :loading="positions.loading" />
    </div>
  </section>
</template>
