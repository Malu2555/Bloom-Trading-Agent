<!--
  DashboardView.vue — portfolio overview (the landing page).

  Read-heavy: pulls the latest PortfolioSnapshot + recent decisions and renders
  them as StatCards + a small feed. Charts (equity curve) and live updates are
  later passes; the layout is already in place.
-->
<script setup>
import { ref, onMounted } from 'vue'
import { usePortfolioStore } from '@/stores/portfolio'
import { useDecisionsStore } from '@/stores/decisions'
import { api } from '@/api'
import StatCard from '@/components/StatCard.vue'

const portfolio = usePortfolioStore()
const decisions = useDecisionsStore()

const agentRunning = ref(false)
const syncing = ref(false)
const lastResult = ref(null)
const agentError = ref(null)

const fmt = (n, dp = 2) =>
  Number(n).toLocaleString('en-US', {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })

async function runAgent() {
  agentRunning.value = true
  agentError.value = null
  lastResult.value = null
  try {
    // Safe default: dry-run (perceives + reasons, never submits live orders).
    const result = await api.runAgent({ dry_run: true })
    lastResult.value = result
    // Refresh the data feeds so the new decision + snapshot appear right away.
    await decisions.fetch()
    await portfolio.fetch()
  } catch (e) {
    console.error('Agent cycle failed:', e)
    agentError.value = e?.message || String(e)
  } finally {
    agentRunning.value = false
  }
}

async function syncFromBroker() {
  syncing.value = true
  agentError.value = null
  try {
    // Pull live Alpaca state into the tracking tables (positions/trades/etc).
    await api.refresh()
    await portfolio.fetch()
    await decisions.fetch()
  } catch (e) {
    console.error('Broker refresh failed:', e)
    agentError.value = e?.message || String(e)
  } finally {
    syncing.value = false
  }
}

onMounted(() => {
  portfolio.fetch()
  decisions.fetch()
})
</script>

<template>
  <section class="flex-col gap-6 p-6">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Dashboard</h1>
        <p class="text-sm text-muted">Portfolio overview</p>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn btn-ghost text-sm" @click="portfolio.fetch" :disabled="syncing || agentRunning">
          Refresh
        </button>
        <button class="btn btn-success text-sm" @click="syncFromBroker" :disabled="syncing || agentRunning">
          {{ syncing ? 'Syncing…' : 'Sync from Alpaca' }}
        </button>
        <button class="btn btn-primary text-sm" @click="runAgent" :disabled="syncing || agentRunning">
          {{ agentRunning ? 'Running…' : 'Run Agent' }}
        </button>
      </div>
    </header>

    <p v-if="portfolio.error" class="text-sm text-danger">{{ portfolio.error }}</p>
    <p v-if="agentError" class="text-sm text-danger">Agent cycle failed: {{ agentError }}</p>

    <!-- last agent decision summary -->
    <div v-if="lastResult" class="card p-4">
      <h2 class="text-sm font-semibold mb-2">Last agent decision</h2>
      <div class="flex flex-wrap items-center gap-2 text-sm">
        <span class="font-medium">{{ lastResult.action }}</span>
        <span v-if="lastResult.confidence != null" class="text-muted">
          conf {{ Number(lastResult.confidence).toFixed(2) }}
        </span>
        <span v-if="lastResult.source" class="text-muted">via {{ lastResult.source }}</span>
        <span v-if="lastResult.degraded" class="text-warning">(degraded)</span>
      </div>
      <p v-if="lastResult.justification" class="text-sm text-muted mt-1">
        {{ lastResult.justification }}
      </p>
    </div>

    <!-- key metrics -->
    <div class="grid gap-4 grid-cols-1 md:grid-cols-4">
      <StatCard label="Equity" :value="fmt(portfolio.equity)" />
      <StatCard
        label="Unrealized P&L"
        :value="fmt(portfolio.pnl)"
        :tone="portfolio.pnl >= 0 ? 'positive' : 'negative'"
      />
      <StatCard
        label="Day P&L"
        :value="fmt(portfolio.dayPnl)"
        :tone="portfolio.dayPnl >= 0 ? 'positive' : 'negative'"
      />
      <StatCard
        label="Positions"
        :value="portfolio.snapshot?.num_positions ?? '—'"
        sub="option positions hidden in prototype"
      />
    </div>

    <!-- recent decisions -->
    <div class="card">
      <h2 class="text-lg font-semibold mb-4">Recent decisions</h2>
      <ul v-if="decisions.items.length" class="flex-col gap-2 text-sm">
        <li
          v-for="d in decisions.items.slice(0, 5)"
          :key="d.run_id"
          class="flex items-center justify-between border-b pb-2"
        >
          <span class="font-medium">{{ d.action }}</span>
          <span class="text-muted">{{ new Date(d.created_at).toLocaleString() }}</span>
          <span v-if="d.confidence != null" class="text-muted">conf {{ Number(d.confidence).toFixed(2) }}</span>
        </li>
      </ul>
      <p v-else class="text-sm text-muted">No decisions yet. Run an agent cycle.</p>
    </div>
  </section>
</template>
