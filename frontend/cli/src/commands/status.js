/**
 * commands/status.js — portfolio overview.
 */
import chalk from 'chalk'
import { api } from '../api.js'
import { usd, signed } from '../formatters.js'

export function register(program) {
  program
    .command('status')
    .description('Show portfolio summary')
    .action(async () => {
      try {
        const p = await api.getPortfolio()
        console.log(chalk.bold('Portfolio'))
        console.log(`  Equity:          ${usd(p.equity)}`)
        console.log(`  Cash:            ${usd(p.cash)}`)
        console.log(`  Buying Power:    ${usd(p.buying_power)}`)
        console.log(`  Unrealized P&L:  ${signed(p.unrealized_pl)}`)
        console.log(`  Day P&L:         ${signed(p.day_pnl)}`)
        console.log(`  Positions:       ${p.num_positions}`)
        console.log(`  At risk:         ${usd(p.at_risk_exposure)}`)
        console.log(`  As of:           ${new Date(p.created_at).toLocaleString()}`)
      } catch (err) {
        console.error(chalk.red(`status failed: ${err.message}`))
        process.exitCode = 1
      }
    })
}
