#!/usr/bin/env node
/**
 * trade-cli — action-heavy CLI for the AI trading agent.
 *
 * The CLI is the "30% action" side of the frontend: it complements the
 * read-heavy dashboard with deterministic operations (run agent cycle, refresh
 * from broker, emergency flatten/close/hedge).
 *
 * Commands live in ../src/commands/*.js and are registered below. The pattern
 * mirrors the backend's REST surface (backend/api/routes.py).
 */
import 'dotenv/config' // load ../.env -> process.env

import { readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, extname } from 'node:path'
import { Command } from 'commander'

const program = new Command()
const __dirname = dirname(fileURLToPath(import.meta.url))

program
  .name('trade')
  .description('AI trading agent CLI')
  .version('0.1.0')

/** Auto-register every module in src/commands — each exports register(program). */
for (const file of readdirSync(join(__dirname, '../src/commands'))) {
  if (extname(file) === '.js') {
    const mod = await import(`../src/commands/${file}`)
    mod.register?.(program)
  }
}

program.parse(process.argv)

// friendly message when no route matched
if (!process.argv.slice(2).length) {
  program.outputHelp()
}
