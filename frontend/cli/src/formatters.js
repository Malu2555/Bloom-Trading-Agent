/**
 * formatters.js — shared CLI rendering helpers.
 *
 * Thin wrappers over cli-table3 + chalk so commands can output consistent
 * tables and colored numbers without repeating markup.
 */
import Table from 'cli-table3'
import chalk from 'chalk'

/** Build a bordered table from column headers + array of row arrays. */
export function renderTable(headers, rows) {
  const table = new Table({
    head: headers.map((h) => chalk.bold(chalk.gray(h))),
    colAligns: headers.map((h, i) => (i > 0 ? 'right' : 'left')),
  })
  for (const row of rows) table.push(row)
  return table.toString()
}

/** Format a number as USD, or '—' when null/undefined. */
export function usd(value, dp = 2) {
  const n = Number(value)
  return Number.isFinite(n) ? `$${n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })}` : '—'
}

/** Color a signed number: green >=0, red < 0. */
export function signed(n, dp = 2) {
  const num = Number(n)
  if (!Number.isFinite(num)) return '—'
  const text = `${num > 0 ? '+' : ''}${num.toFixed(dp)}`
  return num >= 0 ? chalk.green(text) : chalk.red(text)
}

/** Color the action word per the agent's known action set. */
export function actionColor(action) {
  switch (action) {
    case 'hedge':
    case 'buy':
    case 'cover':
      return chalk.green(action)
    case 'sell':
    case 'unwind':
    case 'flat':
    case 'emergency_flat':
      return chalk.red(action)
    case 'hold':
      return chalk.gray(action)
    default:
      return chalk.cyan(action)
  }
}

/** Verify two objects have equal shallow keys/values — for testing. */
export function assertShallowEqual(a, b) {
  const ka = Object.keys(a).sort()
  const kb = Object.keys(b).sort()
  if (ka.join(',') !== kb.join(',')) throw new Error(`key mismatch: ${ka} vs ${kb}`)
  for (const k of ka) {
    if (a[k] !== b[k]) throw new Error(`value mismatch on "${k}": ${a[k]} vs ${b[k]}`)
  }
  return true
}
