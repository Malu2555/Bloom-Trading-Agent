/**
 * commands/trades.js — recent order history.
 */
import chalk from 'chalk'
import { api } from '../api.js'
import { renderTable, usd } from '../formatters.js'

export function register(program) {
  program
    .command('trades')
    .description('List recent trades')
    .option('-l, --limit <n>', 'number of trades', '100')
    .action(async (opts) => {
      try {
        const rows = await api.getTrades(Number(opts.limit))
        const table = renderTable(
          ['Time', 'Symbol', 'Side', 'Qty', 'Fill', 'Status', 'Strategy'],
          rows.map((r) => [
            new Date(r.created_at).toLocaleString(),
            r.symbol,
            chalk.cyan(r.side),
            r.qty,
            usd(r.filled_avg_price),
            r.status,
            r.strategy,
          ]),
        )
        console.log(table)
      } catch (err) {
        console.error(chalk.red(`trades failed: ${err.message}`))
        process.exitCode = 1
      }
    })
}
