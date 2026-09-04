/**
 * commands/refresh.js — pull live Alpaca state into the tracking tables.
 */
import chalk from 'chalk'
import ora from 'ora'
import { api } from '../api.js'

export function register(program) {
  program
    .command('refresh')
    .description('Sync live broker state into local tables')
    .action(async () => {
      const spinner = ora('Refreshing from broker…').start()
      try {
        const result = await api.refresh()
        spinner.succeed('Refresh complete')
        console.log(`  Updated positions: ${result.updated_positions}`)
        console.log(`  Account equity:    ${result.account_equity}`)
      } catch (err) {
        spinner.fail('Refresh failed')
        console.error(chalk.red(`  ${err.message}`))
        process.exitCode = 1
      }
    })
}
