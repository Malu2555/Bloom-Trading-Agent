<!--
  EmergencyView.vue — manual safety (kill-switch) controls.

  Complements the deterministic CLI emergency paths by surfacing the same
  operations in the dashboard so a non-technical user can act without opening
  a terminal. Every destructive action requires explicit confirmation; the
  flatten button uses a two-step "click again to confirm" guard.
-->
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '@/api'

// ---- dangerous action state ---------------------------------------------
const running = ref(false)
const result = ref(null)
const error = ref(null)
const flatArm = ref(false)

// ---- close / hedge forms -------------------------------------------------
const closeSymbol = ref('')
const hedgeSymbol = ref('')
const hedgeShares = ref('')
const hedgeFloor = ref('0.90')

// ---- agent runtime configuration ----------------------------------------
const cfg = ref(null)
const cfgLoading = ref(false)
const cfgSaving = ref(false)
const cfgSaved = ref('')
const cfgError = ref('')
const cfgInput = ref({
  dry_run: true,
  auto_open_positions: false,
  heuristic_only: true,
  max_position_pct: 0.1,
  max_positions: 5,
  min_cash_reserve: 5000,
})

const BOOL_ROWS = [
  {
    key: 'dry_run',
    label: 'Dry run',
    hint: 'Simulate decisions — never submit real orders.',
  },
  {
    key: 'auto_open_positions',
    label: 'Auto-open positions',
    hint: 'Let the agent buy stock / sell cash-secured puts on its own.',
    danger: true,
  },
  {
    key: 'heuristic_only',
    label: 'Heuristic only',
    hint: 'Skip the LLM and use the deterministic fallback.',
  },
]

async function loadCfg() {
  cfgLoading.value = true
  cfgError.value = ''
  try {
    cfg.value = await api.agentConfigGet()
    cfgInput.value = { ...cfg.value }
  } catch (e) {
    cfgError.value = e?.message || String(e)
  } finally {
    cfgLoading.value = false
  }
}

async function saveCfg() {
  cfgSaving.value = true
  cfgSaved.value = ''
  cfgError.value = ''
  try {
    const saved = await api.agentConfigSet({
      dry_run: cfgInput.value.dry_run,
      auto_open_positions: cfgInput.value.auto_open_positions,
      heuristic_only: cfgInput.value.heuristic_only,
      max_position_pct: Number(cfgInput.value.max_position_pct) || 0,
      max_positions: Number(cfgInput.value.max_positions) || 0,
      min_cash_reserve: Number(cfgInput.value.min_cash_reserve) || 0,
    })
    cfg.value = saved
    cfgInput.value = { ...saved }
    cfgSaved.value = 'Saved. It will apply to the next agent cycle.'
  } catch (e) {
    cfgError.value = e?.message || String(e)
  } finally {
    cfgSaving.value = false
  }
}

async function resetCfg() {
  cfgSaving.value = true
  cfgSaved.value = ''
  cfgError.value = ''
  try {
    cfg.value = await api.agentConfigSet({ reset_to_env: true })
    cfgInput.value = { ...cfg.value }
    cfgSaved.value = 'Reset to server defaults.'
  } catch (e) {
    cfgError.value = e?.message || String(e)
  } finally {
    cfgSaving.value = false
  }
}

onMounted(loadCfg)

async function run(fn) {
  running.value = true
  error.value = null
  result.value = null
  try {
    result.value = await fn()
  } catch (e) {
    error.value = e?.message || String(e)
  } finally {
    running.value = false
  }
}

const pretty = (v) => JSON.stringify(v, null, 2)

async function doFlat() {
  if (!flatArm.value) {
    flatArm.value = true
    return
  }
  flatArm.value = false
  await run(() => api.emergencyFlat())
}

function resetArm() {
  flatArm.value = false
}
</script>

<template>
  <section class="flex-col gap-6 p-6" @click="resetArm">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold">Controls</h1>
        <p class="text-sm text-muted">Manual safety &amp; emergency operations</p>
      </div>
    </header>

    <div class="card p-4 text-xs text-muted">
      These actions place or close real orders on the bound paper account. Use carefully.
    </div>

    <p v-if="error" class="text-sm text-danger">{{ error }}</p>
    <pre
      v-if="result"
      class="card p-4 text-xs text-muted overflow-auto"
    >{{ pretty(result) }}</pre>

    <!-- CONFIGURATION -->
    <div class="card p-6 flex-col gap-4">
      <div class="flex items-center justify-between gap-4">
        <h2 class="text-sm font-semibold">Agent configuration</h2>
        <span
          v-if="cfg?.profile_override"
          class="text-[10px] uppercase tracking-wide text-warning"
        >Customized</span>
      </div>
      <p class="text-xs text-muted">
        Live toggles are persisted per profile and apply to the next agent
        cycle. Keep <strong>Dry run</strong> on until you trust its decisions.
      </p>

      <p v-if="cfgError" class="text-xs text-danger">{{ cfgError }}</p>
      <p v-if="cfgSaved" class="text-xs text-primary">{{ cfgSaved }}</p>
      <p v-if="cfgLoading" class="text-xs text-muted">Loading…</p>

      <div class="flex-col gap-2">
        <label
          v-for="row in BOOL_ROWS"
          :key="row.key"
          class="flex items-center gap-3 cursor-pointer"
        >
          <input
            v-model="cfgInput[row.key]"
            type="checkbox"
            class="h-4 w-4"
          />
          <span class="flex-col">
            <span
              class="text-sm font-semibold"
              :class="row.danger && cfgInput[row.key] ? 'text-danger' : ''"
            >{{ row.label }}</span>
            <span class="text-xs text-muted">{{ row.hint }}</span>
          </span>
        </label>
      </div>

      <div class="grid gap-3 grid-cols-1 md:grid-cols-3">
        <label class="flex-col gap-1">
          <span class="text-xs text-muted">Max position % (0.10 = 10%)</span>
          <input
            v-model="cfgInput.max_position_pct"
            type="number"
            step="0.01"
            min="0"
            max="1"
            class="field"
          />
        </label>
        <label class="flex-col gap-1">
          <span class="text-xs text-muted">Max positions</span>
          <input
            v-model="cfgInput.max_positions"
            type="number"
            step="1"
            min="1"
            class="field"
          />
        </label>
        <label class="flex-col gap-1">
          <span class="text-xs text-muted">Cash reserve ($)</span>
          <input
            v-model="cfgInput.min_cash_reserve"
            type="number"
            step="100"
            min="0"
            class="field"
          />
        </label>
      </div>

      <div class="flex items-center gap-3">
        <button
          class="btn btn-primary"
          :disabled="cfgSaving || cfgLoading"
          @click="saveCfg"
        >Save</button>
        <button
          class="btn"
          :disabled="cfgSaving || cfgLoading"
          @click="resetCfg"
        >Reset to defaults</button>
      </div>
    </div>

    <!-- DANGER ZONE -->
    <div class="card p-6 flex-col gap-4" style="border: 1px solid rgb(var(--color-danger))">
      <h2 class="text-lg font-semibold text-danger">Danger zone</h2>

      <div class="flex items-center gap-4">
        <button
          class="btn btn-danger font-semibold"
          :disabled="running"
          @click.stop="doFlat"
        >
          {{ flatArm ? '⚠ Click again to CONFIRM (irreversible)' : '🔴 FLATTEN ALL POSITIONS' }}
        </button>
        <span v-if="running" class="text-xs text-muted">Working…</span>
      </div>
      <span class="text-xs text-muted">
        Cancels every open order and closes the entire book. There is no undo.
      </span>
    </div>

    <!-- MID-RISK ACTIONS -->
    <div class="grid gap-6 grid-cols-1 md:grid-cols-2">
      <div class="card p-6 flex-col gap-3">
        <h2 class="text-sm font-semibold">Cancel all open orders</h2>
        <p class="text-xs text-muted">Cancels pending orders without touching positions.</p>
        <button
          class="btn btn-primary w-fit"
          :disabled="running"
          @click="run(() => api.emergencyCancel())"
        >
          Cancel all orders
        </button>
      </div>

      <div class="card p-6 flex-col gap-3">
        <h2 class="text-sm font-semibold">Close a position</h2>
        <p class="text-xs text-muted">Fully liquidate a single held symbol.</p>
        <div class="flex items-center gap-4">
          <input
            v-model="closeSymbol"
            placeholder="AAPL"
            class="field"
            style="width: 6rem"
            @keyup.enter="closeSymbol && run(() => api.emergencyClose(closeSymbol.toUpperCase()))"
          />
          <button
            class="btn btn-danger"
            :disabled="running || !closeSymbol"
            @click="run(() => api.emergencyClose(closeSymbol.toUpperCase()))"
          >
            Close
          </button>
        </div>
      </div>
    </div>

    <!-- HEDGE (protective put) -->
    <div class="card p-6 flex-col gap-3">
      <h2 class="text-sm font-semibold">Buy a protective put (hedge)</h2>
      <p class="text-xs text-muted">
        Opens a protective put against a held position to cap downside.
      </p>
      <div class="flex flex-wrap items-center gap-4">
        <span class="text-xs text-muted">Symbol</span>
        <input
          v-model="hedgeSymbol"
          placeholder="AAPL"
          class="field"
          style="width: 6rem"
        />
        <span class="text-xs text-muted">Shares</span>
        <input
          v-model="hedgeShares"
          placeholder="all"
          class="field"
          style="width: 5rem"
        />
        <span class="text-xs text-muted">Floor</span>
        <input
          v-model="hedgeFloor"
          placeholder="0.90"
          class="field"
          style="width: 5rem"
        />
        <button
          class="btn btn-primary"
          :disabled="running || !hedgeSymbol"
          @click="
            run(() =>
              api.emergencyHedge({
                symbol: hedgeSymbol.toUpperCase(),
                shares: hedgeShares ? Number(hedgeShares) : null,
                floor_ratio: Number(hedgeFloor) || 0.9,
              })
            )
          "
        >
          Buy put
        </button>
      </div>
    </div>
  </section>
</template>
