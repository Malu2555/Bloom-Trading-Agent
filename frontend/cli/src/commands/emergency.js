/**
 * commands/emergency.js — dangerous, deterministic fallback actions.
 *
 * These mirror backend/api/routes.py (/emergency/*) and require explicit
 * confirmation via inquirer unless --yes is passed. Ship carefully.
 */
import chalk from 'chalk'
import ora from 'ora'
import inquirer from 'inquirer'
import { api } from '../api.js'

async function confirmOrAbort(message, yes) {
  if (yes) return true
  const { ok } = await inquirer.prompt([{ type: 'confirm', name: 'ok', message }])
  if (!ok) {
    console.log(chalk.yellow('Aborted.'))
    return false
  }
  return true
}

function pretty(result) {
  // backend returns { status, message } or similar dicts — print compactly
  for (const [k, v] of Object.entries(result ?? {})) {
    console.log(`  ${k}: ${String(v)}`)
  }
}

export function register(program) {
  const emergency = program.command('emergency').description('Emergency fallback actions')

  emergency
    .command('flat')
    .description('Close ALL positions and cancel all orders')
    .option('-y, --yes', 'skip confirmation', false)
    .action(async (opts) => {
      if (!(await confirmOrAbort('Close ALL positions? This is irreversible.', opts.yes))) return
      const spinner = ora('Flattening book…').start()
      try {
        const result = await api.emergencyFlat()
        spinner.succeed('Flattened')
        pretty(result)
      } catch (err) {
        spinner.fail('Failed')
        console.error(chalk.red(`  ${err.message}`))
        process.exitCode = 1
      }
    })

  emergency
    .command('close <symbol>')
    .description('Close a single position')
    .option('-y, --yes', 'skip confirmation', false)
    .action(async (symbol, opts) => {
      if (!(await confirmOrAbort(`Close position ${symbol}?`, opts.yes))) return
      const spinner = ora(`Closing ${symbol}…`).start()
      try {
        const result = await api.emergencyClose(symbol)
        spinner.succeed('Closed')
        pretty(result)
      } catch (err) {
        spinner.fail('Failed')
        console.error(chalk.red(`  ${err.message}`))
        process.exitCode = 1
      }
    })

  emergency
    .command('cancel-all')
    .description('Cancel all open orders')
    .option('-y, --yes', 'skip confirmation', false)
    .action(async (opts) => {
      if (!(await confirmOrAbort('Cancel all open orders?', opts.yes))) return
      const spinner = ora('Cancelling orders…').start()
      try {
        const result = await api.emergencyCancel()
        spinner.succeed('Cancelled')
        pretty(result)
      } catch (err) {
        spinner.fail('Failed')
        console.error(chalk.red(`  ${err.message}`))
        process.exitCode = 1
      }
    })

  emergency
    .command('hedge <symbol>')
    .description('Buy a protective put for a held position')
    .option('-s, --shares <n>', 'shares to hedge')
    .option('-p, --spot <n>', 'current spot price')
    .option('-f, --floor <ratio>', 'protection floor ratio', '0.90')
    .option('-y, --yes', 'skip confirmation', false)
    .action(async (symbol, opts) => {
      const payload = {
        symbol,
        floor_ratio: Number(opts.floor),
      }
      if (opts.shares) payload.shares = Number(opts.shares)
      if (opts.spot) payload.spot = Number(opts.spot)

      if (!(await confirmOrAbort(`Buy protective put for ${symbol}?`, opts.yes))) return
      const spinner = ora('Placing hedge…').start()
      try {
        const result = await api.emergencyHedge(payload)
        spinner.succeed('Hedge action complete')
        pretty(result)
      } catch (err) {
        spinner.fail('Failed')
        console.error(chalk.red(`  ${err.message}`))
        process.exitCode = 1
      }
    })
}
