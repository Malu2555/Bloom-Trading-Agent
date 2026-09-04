/**
 * commands/options.js — list open option positions (hedges + speculative).
 */
import chalk from 'chalk'
import { api } from '../api.js'
import { renderTable, usd, signed } from '../formatters.js'

export function register(program) {
  program
    .command('options')
    .description('List open option positions')
    .action(async () => {
      try {
        const rows = await api.getOptions()
        const table = renderTable(
          ['OCC', 'Underlying', 'Type', 'Strike', 'Qty', 'Mark', 'P&L', 'Hedge'],
          rows.map((r) => [
            r.occ_symbol,
            r.underlying,
            r.option_type,
            r.strike,
            r.qty,
            usd(r.mark_price),
            signed(r.unrealized_pl),
            r.is_hedge ? chalk.cyan('hedge') : chalk.gray('spec'),
          ]),
        )
        console.log(table)
      } catch (err) {
        console.error(chalk.red(`options failed: ${err.message}`))
        process.exitCode = 1
      }
    })
}
