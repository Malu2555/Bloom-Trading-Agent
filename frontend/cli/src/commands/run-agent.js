/**
 * commands/run-agent.js — run one agent decision cycle.
 *
 * The primary ACTION command. Defaults to dry-run for safety; the LLM-driven
 * cycle will NOT place live orders unless --no-dry-run is passed.
 */
import chalk from 'chalk'
import ora from 'ora'
import { api } from '../api.js'
import { actionColor } from '../formatters.js'

export function register(program) {
  program
    .command('run-agent')
    .description('Run one agent decision cycle')
    .option('--no-dry-run', 'allow real order execution (DANGER)')
    .option('--heuristic-only', 'skip LLM reasoning, use heuristics only')
    .option('--no-heuristic-only', 'use LLM reasoning (default)')
    .action(async (opts) => {
      const spinner = ora('Running agent cycle…').start()
      try {
        const body = {
          dry_run: opts.dryRun, // true by default
          heuristic_only: opts.heuristicOnly ?? false,
        }
        const s = await api.runAgent(body)
        spinner.succeed('Cycle complete')

        console.log(`\n  Decision: ${actionColor(s.action)}`)
        console.log(`  Confidence: ${s.confidence ?? 'n/a'}`)
        if (s.justification) console.log(`  Rationale: ${s.justification}`)
        console.log(`  Executions: ${Array.isArray(s.executions) ? s.executions.length : 0}`)
        console.log(`  Degraded: ${s.degraded ? chalk.yellow('yes') : chalk.green('no')}`)

        if (opts.dryRun) console.log(chalk.gray('\n  (dry-run — no orders placed)'))
      } catch (err) {
        spinner.fail('Agent cycle failed')
        console.error(chalk.red(`  ${err.message}`))
        process.exitCode = 1
      }
    })
}
