/**
 * commands/jobs.js — background JobRun statuses.
 */
import chalk from 'chalk'
import { api } from '../api.js'
import { renderTable } from '../formatters.js'

const statusColor = (s) =>
  s === 'success' ? chalk.green(s) : s === 'failed' ? chalk.red(s) : s === 'running' ? chalk.yellow(s) : chalk.gray(s)

export function register(program) {
  program
    .command('jobs')
    .description('List background jobs')
    .option('-l, --limit <n>', 'number of jobs', '20')
    .action(async (opts) => {
      try {
        const rows = await api.getJobs(Number(opts.limit))
        const table = renderTable(
          ['Name', 'Status', 'Started', 'Finished', 'Message'],
          rows.map((r) => [
            r.name,
            statusColor(r.status),
            r.started_at ? new Date(r.started_at).toLocaleString() : '—',
            r.finished_at ? new Date(r.finished_at).toLocaleString() : '—',
            r.message || '',
          ]),
        )
        console.log(table)
      } catch (err) {
        console.error(chalk.red(`jobs failed: ${err.message}`))
        process.exitCode = 1
      }
    })
}
