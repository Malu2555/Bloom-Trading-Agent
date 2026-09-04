/**
 * commands/hedges.js — active option hedges.
 */
import chalk from 'chalk'
import { api } from '../api.js'
import { renderTable, usd, signed } from '../formatters.js'

export function register(program) {
  program
    .command('hedges')
    .description('List active hedges')
    .action(async () => {
      try {
        const rows = await api.getHedges()
        const table = renderTable(
          ['Underlying', 'OCC', 'Contracts', 'Strike', 'Expiry', 'Premium', 'Floor', 'Status', 'P&L'],
          rows.map((r) => [
            r.underlying,
            r.occ_symbol,
            r.contracts,
            r.strike,
            r.expiry,
            usd(r.premium_total),
            r.protection_floor,
            r.status,
            signed(r.current_pnl),
          ]),
        )
        console.log(table)
      } catch (err) {
        console.error(chalk.red(`hedges failed: ${err.message}`))
        process.exitCode = 1
      }
    })
}
