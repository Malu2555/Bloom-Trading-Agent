<!--
  TradesView.vue — order history table.

  Wires the trades store into the generic DataTable. Add date/symbol filtering
  and CSV export in a later pass.
-->
<script setup>
import { onMounted } from 'vue'
import { useTradesStore } from '@/stores/trades'
import DataTable from '@/components/DataTable.vue'

const trades = useTradesStore()

const columns = [
  { key: 'created_at', label: 'Time', sortable: true },
  { key: 'symbol', label: 'Symbol', sortable: true },
  { key: 'side', label: 'Side' },
  { key: 'order_type', label: 'Type' },
  { key: 'qty', label: 'Qty', align: 'right' },
  { key: 'filled_avg_price', label: 'Fill', align: 'right' },
  { key: 'status', label: 'Status' },
  { key: 'strategy', label: 'Strategy' },
  { key: 'is_hedge', label: 'Hedge' },
]

onMounted(() => trades.fetch())
</script>

<template>
  <section class="flex-col gap-6 p-6">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Trades</h1>
        <p class="text-sm text-muted">Recent order history</p>
      </div>
      <button class="btn btn-ghost text-sm" @click="trades.fetch">Refresh</button>
    </header>

    <div class="card">
      <DataTable :columns="columns" :rows="trades.items" :loading="trades.loading">
        <template #cell="{ row, columnKey }">
          <template v-if="columnKey === 'created_at'">
            {{ new Date(row.created_at).toLocaleString() }}
          </template>
          <span
            v-else-if="columnKey === 'side'"
            :class="String(row.side).toLowerCase() === 'buy' ? 'positive' : 'negative'"
          >
            {{ row.side }}
          </span>
          <template v-else>{{ row[columnKey] }}</template>
        </template>
      </DataTable>
    </div>
  </section>
</template>
