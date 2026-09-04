/**
 * commands/decisions.js — agent decision history.
 */
import chalk from 'chalk'
import { api } from '../api.js'
import { renderTable, actionColor } from '../formatters.js'

export function register(program) {
  program
    .command('decisions')
    .description('List recent agent decisions')
    .option('-l, --limit <n>', 'number of decisions', '20')
    .action(async (opts) => {
      try {
        const rows = await api.getDecisions(Number(opts.limit))
        const table = renderTable(
          ['Time', 'Action', 'Conf', 'Model', 'Degraded'],
          rows.map((r) => [
            new Date(r.created_at).toLocaleString(),
            actionColor(r.action),
            r.confidence != null ? Number(r.confidence).toFixed(2) : '—',
            r.model,
            r.degraded ? chalk.yellow('yes') : chalk.green('no'),
          ]),
        )
        console.log(table)
      } catch (err) {
        console.error(chalk.red(`decisions failed: ${err.message}`))
        process.exitCode = 1
      }
    })
}
