"""Run one autonomous agent decision cycle from the command line.

Examples::

    python manage.py run_agent                 # DRY_RUN + heuristic (safe)
    python manage.py run_agent --live          # allow real orders (DANGER)
    python manage.py run_agent --llm           # ask the configured LLM
    python manage.py run_agent --json          # machine-readable output
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from agent import brain


class Command(BaseCommand):
    help = "Run one autonomous AI trading agent cycle (protective-put hedging)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--live",
            action="store_true",
            default=False,
            help="Allow real order submission (default is DRY_RUN).",
        )
        parser.add_argument(
            "--llm",
            action="store_true",
            default=False,
            help="Use the configured LLM instead of the heuristic fallback.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=False,
            help="Print the summary as JSON.",
        )

    def handle(self, *args, **options):
        dry_run = not options["live"]
        heuristic_only = not options["llm"]
        self.stdout.write(
            f"Running agent cycle (dry_run={dry_run}, heuristic_only={heuristic_only})..."
        )
        summary = brain.run_agent_cycle_sync(
            dry_run=dry_run, heuristic_only=heuristic_only
        )
        if options["json"]:
            self.stdout.write(json.dumps(summary, indent=2, default=str))
            return
        self.stdout.write(self.style.MIGRATE_HEADING("Agent cycle summary:"))
        self.stdout.write(json.dumps(summary, indent=2, default=str))
        if summary.get("errors"):
            self.stdout.write(self.style.ERROR(f"Errors: {summary['errors']}"))
