<!--
  DataTable.vue — generic, sortable table for list resources.

  Props:
    columns - [{ key, label, sortable?, align? }]
              `key` is a field on each row, or a render key for slots.
    rows    - array of row objects
    loading - shows a loading state
    empty   - text shown when rows is empty

  Slots:
    #cell(row, columnKey) - optional custom cell rendering

  This is intentionally minimal for the prototype. Extend with filtering,
  pagination, and row-click as needed.
-->
<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  columns: { type: Array, required: true }, // [{ key, label, sortable, align }]
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  empty: { type: String, default: 'No rows to display.' },
})

const emit = defineEmits(['sort-change'])

const sortKey = ref(null)
const sortDir = ref('asc')

/** Value of a row field; supports dotted keys like "a.b". */
function cellValue(row, key) {
  return key.split('.').reduce((acc, k) => (acc == null ? acc : acc[k]), row)
}

/** Sorted rows based on the active sortKey. Simple, prototype-level. */
const sortedRows = computed(() => {
  if (!sortKey.value) return props.rows
  const key = sortKey.value
  return [...props.rows].sort((a, b) => {
    const va = cellValue(a, key)
    const vb = cellValue(b, key)
    if (va == null || vb == null) return 0
    const cmp = String(va).localeCompare(String(vb), undefined, { numeric: true })
    return sortDir.value === 'asc' ? cmp : -cmp
  })
})

function toggleSort(col) {
  if (!col.sortable) return
  emit('sort-change', col.key)
  if (sortKey.value === col.key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = col.key
    sortDir.value = 'asc'
  }
}
</script>

<template>
  <div class="overflow-auto w-full">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b">
          <th
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-2 text-left text-xs text-muted font-medium cursor-pointer select-none"
            :class="col.align === 'right' ? 'text-right' : ''"
            @click="toggleSort(col)"
          >
            {{ col.label }}
            <span v-if="sortKey === col.key">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading">
          <td :colspan="columns.length" class="px-4 py-6 text-center text-muted text-sm">
            Loading…
          </td>
        </tr>
        <tr v-else-if="sortedRows.length === 0">
          <td :colspan="columns.length" class="px-4 py-6 text-center text-muted text-sm">
            {{ empty }}
          </td>
        </tr>
        <tr
          v-for="(row, i) in sortedRows"
          :key="i"
          class="border-b hover:bg-abg"
        >
          <td
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-2 text-left"
            :class="col.align === 'right' ? 'text-right' : ''"
          >
            <slot name="cell" :row="row" :column-key="col.key">
              {{ cellValue(row, col.key) }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
