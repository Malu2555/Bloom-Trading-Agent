"""
Emergency fallback infrastructure layer.

``cli_runner.py`` spawns raw background subprocess blocks targeting the Alpaca
CLI for hardcoded emergency trading tasks (flat the book, cancel orders, buy a
protective put NOW). These paths are deterministic - there is NO model inference
or discretion - so they still work when the LLM or the API is degraded.

Execution strategy:
1. Prefer an ``alpaca`` CLI binary on PATH (via subprocess), because that is the
   "hardcoded" contract the module documents.
2. Fall back to the Alpaca MCP server through ``AlpacaSyncClient`` so the
   emergency actions work on this stack without a separate CLI install.

Every action is also written to the tracking tables for an audit trail.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from agent import brain  # noqa: F401  (kept so collateral helpers stay importable)
from agent.brain import (
    AgentConfig,
    compute_protective_put,
    select_protective_put_contract,
)

logger = logging.getLogger(__name__)


def _cli_available() -> bool:
    return shutil.which("alpaca") is not None


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


class EmergencyExecutor:
    """Hardcoded emergency trading actions with a CLI-first, MCP-fallback path."""

    def __init__(
        self,
        use_cli: bool | None = None,
        *,
        credentials=None,
        owner=None,
    ) -> None:
        self._use_cli = _cli_available() if use_cli is None else bool(use_cli)
        self._credentials = credentials  # per-user Alpaca keys (isolation)
        self._owner = owner  # UserProfile this executor's audit rows belong to
        self._mcp = None  # lazily built AlpacaSyncClient

    def _client(self):
        if self._mcp is None:
            from agent.mcp_client import AlpacaSyncClient

            self._mcp = AlpacaSyncClient(credentials=self._credentials)
        return self._mcp

    # -- subprocess (Alpaca CLI) -------------------------------------------
    def _run_cli(self, *args: str, input_json: dict | None = None) -> dict:
        if not self._use_cli:
            raise OSError("alpaca CLI not found on PATH")
        cmd = ["alpaca", *args]
        env = dict(os.environ)
        if self._credentials is not None:
            env.update(self._credentials.to_environ())
        proc = subprocess.run(
            cmd,
            input=json.dumps(input_json) if input_json is not None else None,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        try:
            return json.loads(proc.stdout or "{}")
        except ValueError:
            return {"stdout": proc.stdout, "stderr": proc.stderr, "rc": proc.returncode}

    # -- audit plumbing ----------------------------------------------------
    def _record_order(self, symbol: str, payload: dict) -> None:
        try:
            from agent.models import OrderRecord

            OrderRecord.objects.create(
                order_id=payload.get("id", ""),
                symbol=symbol or "?",
                side="buy",
                order_type="market",
                qty=payload.get("contracts", 0),
                status=payload.get("status", "unknown"),
                strategy=OrderRecord.STRATEGY_EMERGENCY,
                is_hedge=True,
                source=OrderRecord.SOURCE_EMERGENCY_CLI,
                payload=payload,
                owner_id=(self._owner.id if self._owner is not None else None),
            )
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            logger.warning("Order audit write failed: %s", exc)

    # -- public emergency actions ------------------------------------------
    def cancel_all(self) -> dict:
        if self._use_cli:
            try:
                return {"source": "cli", "result": self._run_cli("orders", "cancel")}
            except Exception as exc:  # noqa: BLE001
                logger.warning("CLI cancel_all failed, falling back to MCP: %s", exc)
        try:
            result = self._client().cancel_all_orders()
            return {"source": "mcp", "status": "ok", "result": result}
        except Exception as exc:
            logger.exception("cancel_all failed")
            return {"source": "mcp", "status": "error", "error": str(exc)}

    def close_position(self, symbol: str) -> dict:
        if self._use_cli:
            try:
                return {
                    "source": "cli",
                    "symbol": symbol,
                    "result": self._run_cli("position", "close", symbol),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("CLI close failed, falling back to MCP: %s", exc)
        try:
            result = self._client().close_position(symbol)
            return {"source": "mcp", "symbol": symbol, "status": "ok", "result": result}
        except Exception as exc:
            logger.exception("close_position(%s) failed", symbol)
            return {
                "source": "mcp",
                "symbol": symbol,
                "status": "error",
                "error": str(exc),
            }

    def close_all(self) -> dict:
        events = {"cancel": self.cancel_all(), "closed": []}
        try:
            events["closed"] = self._client().close_all_positions()
        except Exception as exc:
            logger.exception("close_all failed")
            events["close_error"] = str(exc)
        return events

    def protect_position(
        self,
        symbol: str,
        shares: Decimal | None = None,
        spot: Decimal | None = None,
        floor_ratio: Decimal = Decimal("0.90"),
    ) -> dict:
        """Hardcoded protective put: buy a put against a long position now."""
        client = self._client()
        try:
            positions = client.get_positions() or []
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"could not read positions: {exc}"}
        entry = next((p for p in positions if p.get("symbol") == symbol), None)
        if entry is None:
            return {"status": "error", "error": f"no position for {symbol}"}

        held_shares = _dec(shares) if shares else _dec(entry.get("qty"))
        held_spot = _dec(spot) if spot else _dec(entry.get("current_price"))
        if held_shares <= 0 or held_spot <= 0:
            return {
                "status": "error",
                "error": "unable to derive shares/spot for the hedge",
            }

        cfg = AgentConfig(
            dry_run=False,
            protection_floor_ratio=_dec(floor_ratio),
            hedge_days_to_expiry=30,
        )
        try:
            params = compute_protective_put(held_shares, held_spot, floor_ratio, cfg)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"hedge design failed: {exc}"}

        contracts = params["contracts"]
        if contracts <= 0:
            return {
                "status": "skipped",
                "symbol": symbol,
                "reason": "position smaller than one 100-share contract",
            }

        occ = None
        strike = params["target_strike"]
        try:
            avail = client.get_option_contracts(
                symbol, expiration_date=params["expiry"]
            )
            pick = select_protective_put_contract(
                avail, params["target_strike"], params["expiry"]
            )
            if pick:
                occ = pick["occ_symbol"]
                strike = pick["strike"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Option contracts lookup failed for %s: %s", symbol, exc)

        if occ is None:
            return {
                "status": "skipped",
                "symbol": symbol,
                "reason": "no matching put contract resolved (data may be unavailable)",
                "design": {
                    "target_strike": str(params["target_strike"]),
                    "floor": str(params["floor"]),
                    "expiry": params["expiry"],
                },
            }

        try:
            result = client.place_option_order(
                occ_symbol=occ,
                side="buy",
                qty=contracts,
                position_intent="buy_to_open",
                order_type="market",
            )
        except Exception as exc:
            logger.exception("Emergency hedge order failed")
            return {"status": "error", "error": str(exc)}

        self._record_order(
            occ,
            {
                "id": result.get("id", ""),
                "status": result.get("status"),
                "contracts": contracts,
            },
        )
        try:
            from agent.models import Hedge

            Hedge.objects.create(
                underlying=symbol,
                occ_symbol=occ,
                contracts=contracts,
                strike=strike,
                expiry=date.fromisoformat(params["expiry"]),
                status=Hedge.STATUS_PENDING,
                order_id=result.get("id", ""),
                notes="emergency protective put",
                owner_id=(self._owner.id if self._owner is not None else None),
            )
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            logger.warning("Hedge audit write failed: %s", exc)

        return {
            "status": "ok",
            "source": "cli" if self._use_cli else "mcp",
            "symbol": symbol,
            "occ_symbol": occ,
            "contracts": contracts,
            "strike": str(strike),
            "expiry": params["expiry"],
            "order_id": result.get("id"),
            "order_status": result.get("status"),
        }
