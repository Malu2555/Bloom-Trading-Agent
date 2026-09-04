<!--
  DecisionsView.vue — agent decision history.

  Lists each agent cycle's decision (action, confidence, degraded flag) with a
  click-to-expand for the full summary. Add the run_detail drawer in a later pass.
-->
<script setup>
import { onMounted } from 'vue'
import { useDecisionsStore } from '@/stores/decisions'
import DataTable from '@/components/DataTable.vue'

const decisions = useDecisionsStore()

const columns = [
  { key: 'created_at', label: 'Time', sortable: true },
  { key: 'run_id', label: 'Run' },
  { key: 'model', label: 'Model' },
  { key: 'action', label: 'Action', sortable: true },
  { key: 'confidence', label: 'Confidence', align: 'right' },
  { key: 'degraded', label: 'Degraded' },
  { key: 'summary', label: 'Summary' },
]

onMounted(() => decisions.fetch())
</script>

<template>
  <section class="flex-col gap-6 p-6">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Decisions</h1>
        <p class="text-sm text-muted">Agent decision history</p>
      </div>
      <button class="btn btn-ghost text-sm" @click="decisions.fetch">Refresh</button>
    </header>

    <div class="card">
      <DataTable :columns="columns" :rows="decisions.items" :loading="decisions.loading">
        <template #cell="{ row, columnKey }">
          <template v-if="columnKey === 'created_at'">
            {{ new Date(row.created_at).toLocaleString() }}
          </template>
          <span
            v-else-if="columnKey === 'degraded'"
            :class="row.degraded ? 'text-warning' : 'positive'"
          >
            {{ row.degraded ? 'yes' : 'no' }}
          </span>
          <span v-else-if="columnKey === 'summary'" class="truncate max-w-md inline-block">
            {{ row.summary }}
          </span>
          <template v-else>{{ row[columnKey] }}</template>
        </template>
      </DataTable>
    </div>
  </section>
</template>
