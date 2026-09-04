<!--
  StatCard.vue — small labeled metric card used across dashboard views.

  Props:
    label   - heading text (e.g. "Equity")
    value   - formatted primary value (string | number)
    sub     - optional secondary line (e.g. a delta or "vs yesterday")
    tone    - 'default' | 'positive' | 'negative' | 'muted' (colors the value)

  Styling composes .card from main.css with utility spacing classes.
-->
<script setup>
const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], required: true },
  sub: { type: String, default: '' },
  tone: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'positive', 'negative', 'muted'].includes(v),
  },
})

const toneClass = {
  default: 'text-fg',
  positive: 'positive',
  negative: 'negative',
  muted: 'text-muted',
}[props.tone]
</script>

<template>
  <div class="card flex-col gap-1">
    <span class="text-xs text-muted">{{ label }}</span>
    <span class="text-2xl font-semibold" :class="toneClass">{{ value }}</span>
    <span v-if="sub" class="text-xs text-muted">{{ sub }}</span>
  </div>
</template>
