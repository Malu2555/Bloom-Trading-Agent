<!--
  OptionsView.vue — open option positions.

  Groups option positions (hedges + speculative) in a table. Color-code the
  `is_hedge` flag and add payoff diagrams in a later pass.
-->
<script setup>
import { onMounted } from 'vue'
import { useOptionsStore } from '@/stores/options'
import DataTable from '@/components/DataTable.vue'

const options = useOptionsStore()

const columns = [
  { key: 'occ_symbol', label: 'OCC', sortable: true },
  { key: 'underlying', label: 'Underlying', sortable: true },
  { key: 'option_type', label: 'Type' },
  { key: 'strike', label: 'Strike', align: 'right' },
  { key: 'expiry', label: 'Expiry' },
  { key: 'qty', label: 'Qty', align: 'right' },
  { key: 'mark_price', label: 'Mark', align: 'right' },
  { key: 'unrealized_pl', label: 'P&L', align: 'right' },
  { key: 'is_hedge', label: 'Hedge' },
]

onMounted(() => options.fetch())
</script>

<template>
  <section class="flex-col gap-6 p-6">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Options</h1>
        <p class="text-sm text-muted">Open option positions</p>
      </div>
      <button class="btn btn-ghost text-sm" @click="options.fetch">Refresh</button>
    </header>

    <div class="card">
      <DataTable :columns="columns" :rows="options.items" :loading="options.loading">
        <template #cell="{ row, columnKey }">
          <span
            v-if="columnKey === 'is_hedge'"
            class="badge"
            :class="row.is_hedge ? 'bg-info' : 'bg-abg'"
          >
            {{ row.is_hedge ? 'hedge' : 'spec' }}
          </span>
          <template v-else>{{ row[columnKey] }}</template>
        </template>
      </DataTable>
    </div>
  </section>
</template>
