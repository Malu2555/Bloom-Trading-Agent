/**
 * commands/positions.js — list open positions as a table.
 */
import chalk from 'chalk'
import { api } from '../api.js'
import { renderTable, usd, signed } from '../formatters.js'

export function register(program) {
  program
    .command('positions')
    .description('List open stock positions')
    .action(async () => {
      try {
        const rows = await api.getPositions()
        const table = renderTable(
          ['Symbol', 'Qty', 'Avg', 'Current', 'Mkt Value', 'P&L'],
          rows.map((r) => [
            r.symbol,
            r.qty,
            usd(r.avg_entry_price),
            usd(r.current_price),
            usd(r.market_value),
            signed(r.unrealized_pl),
          ]),
        )
        console.log(table)
      } catch (err) {
        console.error(chalk.red(`positions failed: ${err.message}`))
        process.exitCode = 1
      }
    })
}
