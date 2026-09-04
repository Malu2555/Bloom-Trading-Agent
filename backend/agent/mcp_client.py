"""
MCP client that orchestrates connections to the local ``alpaca-mcp-server``.

Exposes a small, typed-ish async facade over the Alpaca MCP tools the agent and
the emergency CLI rely on. Background/synchronous callers can use the
``AlpacaSyncClient`` wrapper, which spins an event loop per operation.

The Alpaca MCP server wraps every tool result in a trust-boundary envelope::

    {"_alpaca_mcp_security": {...}, "data": <payload>}

We unwrap that envelope and return ``data`` so the rest of the agent never has
to reason about prompt-injection boilerplate.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import uuid
from datetime import timedelta
from typing import Any, Self

try:
    from mcp import ClientSession, StdioServerParameters, stdio_client
except Exception:  # pragma: no cover - dependency not installed  # noqa: BLE001
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Server reads these env vars; we also honour the .env spellings used in this repo.
_ENV_BRIDGE = {
    "ALPACA_API_KEY": "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY": "ALPACA_API_SECRET_KEY",
    "ALPACA_PAPER_TRADE": "ALPACA_PAPER_MODE",
    "ALPACA_BASE_URL": "ALPACA_BASE_URL",
    "DATA_API_URL": "ALPACA_DATA_URL",
}

DEFAULT_TOOLSETS = "account,trading,assets,options-data,stock-data"


class AlpacaMCPError(RuntimeError):
    """Raised when a call to the Alpaca MCP server fails."""


def _server_command() -> str:
    """Return the path to the ``alpaca-mcp-server`` console script."""
    exe = shutil.which("alpaca-mcp-server")
    if exe:
        return exe
    scripts = os.path.join(os.path.dirname(sys.executable), "alpaca-mcp-server")
    for candidate in (scripts, scripts + ".exe"):
        if os.path.exists(candidate):
            return candidate
    raise AlpacaMCPError(
        "alpaca-mcp-server console script not found on PATH. "
        "Activate the virtualenv and install requirements.txt."
    )


def _build_environment(credentials=None) -> dict[str, str]:
    """Merge the current process env (+ per-user credentials) for the server.

    When ``credentials`` (any object exposing ``to_environ()``, e.g.
    ``user.credentials.AlpacaCredentials``) is supplied, its values override the
    inherited env so this subprocess only ever sees *that one user's* keys. This
    is the core isolation boundary preventing cross-user credential leakage.
    """
    env = dict(os.environ)
    if credentials is not None:
        env.update(credentials.to_environ())
    for target, source in _ENV_BRIDGE.items():
        value = env.get(source)
        if value:
            env[target] = value
    # Idempotent: never accidentally trade live.
    paper = env.get("ALPACA_PAPER_TRADE", env.get("ALPACA_PAPER_MODE", "true"))
    env["ALPACA_PAPER_TRADE"] = paper
    env.setdefault("ALPACA_TOOLSETS", DEFAULT_TOOLSETS)
    return env


def build_server_parameters(
    command: str | None = None,
    extra_args: list[str] | None = None,
    credentials=None,
) -> Any:
    """Build stdio parameters pointing at the local Alpaca MCP server."""
    if StdioServerParameters is None:
        raise AlpacaMCPError("The python 'mcp' package is not installed.")
    args = ["--transport", "stdio"]
    if extra_args:
        args.extend(extra_args)
    return StdioServerParameters(
        command=command or _server_command(),
        args=args,
        env=_build_environment(credentials),
    )


def _extract_payload(result: Any) -> Any:
    """Unwrap the Alpaca trust-boundary envelope from a CallToolResult."""
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        if "_alpaca_mcp_security" in structured and "data" in structured:
            return structured["data"]
        return structured
    # Fall back to joining text content blocks (the FastMCP stdio transport
    # returns results as text blocks, not structured_content).
    texts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if texts:
        raw = "\n".join(texts)
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return {"text": raw}
        # Unwrap the trust-boundary envelope in this path too, otherwise
        # account/position fields end up under "data" and the caller sees a
        # zeroed-out account (e.g. "No equity, cash, or positions").
        if (
            isinstance(payload, dict)
            and "_alpaca_mcp_security" in payload
            and "data" in payload
        ):
            return payload["data"]
        return payload
    return {"raw": str(result)}


class AlpacaClient:
    """Async session wrapper around a single MCP connection.

    Use as a context manager::

        async with AlpacaClient() as client:
            account = await client.get_account_info()
    """

    def __init__(
        self,
        command: str | None = None,
        toolsets: str | None = None,
        credentials=None,
    ) -> None:
        self._command = command
        self._toolsets = toolsets
        self._credentials = credentials  # per-user Alpaca keys (isolation)
        # The optional `mcp` import can be absent at runtime; using the runtime
        # fallback value in type annotations trips type checkers.
        self._params: Any | None = None
        self._client = None
        self._session: Any | None = None
        self._timeout = 60.0

    # -- lifecycle -------------------------------------------------------
    async def __aenter__(self) -> Self:
        if stdio_client is None or ClientSession is None:
            raise AlpacaMCPError("The python 'mcp' package is not installed.")
        params = build_server_parameters(self._command, credentials=self._credentials)
        if self._toolsets:
            params.env.setdefault("ALPACA_TOOLSETS", self._toolsets)
        self._params = params
        self._client = stdio_client(params)
        read_stream, write_stream = await self._client.__aenter__()
        self._session = await ClientSession(
            read_stream,
            write_stream,
            # mcp requires a timedelta here (a bare float crashes the handshake).
            read_timeout_seconds=timedelta(seconds=self._timeout),
        ).__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        errors = []
        if self._session is not None:
            try:
                await self._session.__aexit__(exc_type, exc, tb)
            except Exception as e:  # pragma: no cover  # noqa: BLE001
                errors.append(e)
            self._session = None
        if self._client is not None:
            try:
                await self._client.__aexit__(exc_type, exc, tb)
            except Exception as e:  # pragma: no cover  # noqa: BLE001
                errors.append(e)
            self._client = None
        if errors:
            logger.debug("Ignoring MCP teardown errors: %s", errors)

    # -- low level -------------------------------------------------------
    async def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """Call a named MCP tool and return its unwrapped payload."""
        if self._session is None:
            raise AlpacaMCPError("AlpacaClient is not connected.")
        args = arguments or {}
        try:
            result = await self._session.call_tool(name, arguments=args)
        except Exception as e:
            raise AlpacaMCPError(f"tool '{name}' failed: {e}") from e
        if getattr(result, "isError", False):
            raise AlpacaMCPError(
                f"tool '{name}' returned an error: {_extract_payload(result)}"
            )
        return _extract_payload(result)

    # -- account / market -------------------------------------------------
    async def get_account_info(self) -> dict:
        return await self.call_tool("get_account_info")

    async def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        return await self.call_tool(
            "get_portfolio_history",
            {"period": period, "timeframe": timeframe},
        )

    async def get_clock(self) -> dict:
        return await self.call_tool("get_clock")

    async def get_positions(self) -> list:
        data = await self.call_tool("get_all_positions")
        return data if isinstance(data, list) else []

    # -- orders -----------------------------------------------------------
    @staticmethod
    def _client_order_id(prefix: str = "agent") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    async def cancel_all_orders(self) -> dict:
        return await self.call_tool("cancel_all_orders")

    async def place_option_order(
        self,
        occ_symbol: str,
        side: str,
        qty: int,
        position_intent: str,
        limit_price: str | None = None,
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> dict:
        args: dict[str, Any] = {
            "symbol": occ_symbol,
            "side": side,
            "qty": str(qty),
            "position_intent": position_intent,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            args["limit_price"] = str(limit_price)
        args["client_order_id"] = self._client_order_id("opt")
        return await self.call_tool("place_option_order", args)

    async def place_stock_order(
        self,
        symbol: str,
        side: str,
        qty: int | None = None,
        notional: str | None = None,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: str | None = None,
    ) -> dict:
        args: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if qty is not None:
            args["qty"] = str(qty)
        if notional is not None:
            args["notional"] = str(notional)
        if limit_price is not None:
            args["limit_price"] = str(limit_price)
        args["client_order_id"] = self._client_order_id("stk")
        return await self.call_tool("place_stock_order", args)

    async def get_stock_latest_trade(self, symbols: str) -> dict:
        return await self.call_tool("get_stock_latest_trade", {"symbols": symbols})

    # -- positions close -------------------------------------------------
    async def close_position(self, symbol: str) -> dict:
        return await self.call_tool("close_position", {"symbol_or_asset_id": symbol})

    async def close_all_positions(self) -> dict:
        return await self.call_tool("close_all_positions")

    # -- options market data ---------------------------------------------
    async def get_option_contracts(
        self,
        symbol: str,
        strikes: list | None = None,
        expiration_date: str | None = None,
    ) -> list:
        args: dict[str, Any] = {"symbol": symbol}
        if strikes is not None:
            args["strikes"] = [str(s) for s in strikes]
        if expiration_date is not None:
            args["expiration_date"] = expiration_date
        data = await self.call_tool("get_option_contracts", args)
        if isinstance(data, dict):
            data = data.get("option_contracts", data.get("contracts", []))
        return data if isinstance(data, list) else []

    async def get_option_snapshot(self, occ_symbol: str) -> dict:
        return await self.call_tool("get_option_snapshot", {"symbols": occ_symbol})


class AlpacaSyncClient:
    """Synchronous facade for code that cannot ``await`` (CLI, views, healers)."""

    def __init__(
        self,
        command: str | None = None,
        toolsets: str | None = None,
        credentials=None,
    ) -> None:
        self._command = command
        self._toolsets = toolsets
        self._credentials = credentials  # per-user Alpaca keys (isolation)

    def _sync(self, op: str, *args, **kwargs):
        import asyncio
        import threading

        async def _call():
            async with AlpacaClient(
                self._command, self._toolsets, credentials=self._credentials
            ) as client:
                method = getattr(client, op)
                return await method(*args, **kwargs)

        def runner(result: dict):
            try:
                result["_value"] = asyncio.run(_call())
            except Exception as e:  # noqa: BLE001
                result["_error"] = e
            finally:
                result["_done"] = True

        result: dict = {"_done": False, "_value": None, "_error": None}
        try:
            asyncio.get_running_loop()
            # Already inside an event loop (tests/views) - run on a fresh thread.
            t = threading.Thread(target=runner, args=(result,), daemon=True)
            t.start()
            t.join()
        except RuntimeError:
            runner(result)  # no running loop - safe to run inline

        if result["_error"] is not None:
            raise result["_error"]
        return result["_value"]

    def get_account_info(self) -> dict:
        return self._sync("get_account_info")

    def get_positions(self) -> list:
        return self._sync("get_positions")

    def cancel_all_orders(self) -> dict:
        return self._sync("cancel_all_orders")

    def close_position(self, symbol: str) -> dict:
        return self._sync("close_position", symbol)

    def close_all_positions(self) -> dict:
        return self._sync("close_all_positions")

    def place_option_order(
        self,
        occ_symbol: str,
        side: str,
        qty: int,
        position_intent: str,
        limit_price=None,
        order_type: str = "market",
    ) -> dict:
        return self._sync(
            "place_option_order",
            occ_symbol,
            side,
            qty,
            position_intent,
            limit_price=limit_price,
            order_type=order_type,
        )

    def place_stock_order(
        self,
        symbol: str,
        side: str,
        qty: int | None = None,
        notional: str | None = None,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: str | None = None,
    ) -> dict:
        return self._sync(
            "place_stock_order",
            symbol,
            side,
            qty=qty,
            notional=notional,
            order_type=order_type,
            time_in_force=time_in_force,
            limit_price=limit_price,
        )

    def get_stock_latest_trade(self, symbols: str) -> dict:
        return self._sync("get_stock_latest_trade", symbols)

    def get_option_contracts(self, symbol, strikes=None, expiration_date=None) -> list:
        return self._sync(
            "get_option_contracts",
            symbol,
            strikes=strikes,
            expiration_date=expiration_date,
        )

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        return self._sync("get_portfolio_history", period, timeframe)

    def get_clock(self) -> dict:
        return self._sync("get_clock")

    def get_option_snapshot(self, occ_symbol: str) -> dict:
        return self._sync("get_option_snapshot", occ_symbol)
