<!--
  JobsView.vue — background JobRun statuses.

  Shows whether refresh / agent-cycle work is running, with status badges.
  Add auto-polling of the jobs store in a later pass for live status.
-->
<script setup>
import { onMounted } from 'vue'
import { useJobsStore } from '@/stores/jobs'
import DataTable from '@/components/DataTable.vue'

const jobs = useJobsStore()

const columns = [
  { key: 'job_id', label: 'Job' },
  { key: 'name', label: 'Name', sortable: true },
  { key: 'status', label: 'Status' },
  { key: 'started_at', label: 'Started' },
  { key: 'finished_at', label: 'Finished' },
  { key: 'message', label: 'Message' },
]

// Simple status color helper (matches backend STATUS_* set).
const statusTone = (status) =>
  status === 'success' ? 'positive' : status === 'failed' ? 'negative' : status === 'running' ? 'text-warning' : 'text-muted'

onMounted(() => jobs.fetch())
</script>

<template>
  <section class="flex-col gap-6 p-6">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Jobs</h1>
        <p class="text-sm text-muted">Background work</p>
      </div>
      <button class="btn btn-ghost text-sm" @click="jobs.fetch">Refresh</button>
    </header>

    <div class="card">
      <DataTable :columns="columns" :rows="jobs.items" :loading="jobs.loading">
        <template #cell="{ row, columnKey }">
          <template v-if="columnKey === 'status'">
            <span class="badge" :class="statusTone(row.status)">{{ row.status }}</span>
          </template>
          <template v-else-if="columnKey === 'started_at' || columnKey === 'finished_at'">
            {{ row[columnKey] ? new Date(row[columnKey]).toLocaleTimeString() : '—' }}
          </template>
          <template v-else>{{ row[columnKey] }}</template>
        </template>
      </DataTable>
    </div>
  </section>
</template>
