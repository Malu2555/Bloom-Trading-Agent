"""
data_perception.py - the agent's perception layer.

Feeds the agent's decision engine with market data pulled *directly* from
Alpaca (no news/article scraping). It reuses a single pair of unified API
credentials from ``.env`` to build three lazy clients:

- ``stock_client``   -> ``alpaca.data.historical.StockHistoricalDataClient``
- ``option_client``  -> ``alpaca.data.historical.OptionHistoricalDataClient``
- ``trading_client`` -> ``alpaca.trading.client.TradingClient`` (actual orders)

It also provides the ingestion pipeline (chunking -> embedding -> storage) that
persists each perceived fact as a vectorized row in ``EmbeddedRiskContext`` /
the Chroma-backed store, so the LLM can later recall it as semantic knowledge.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)


class _MissingAlpacaDependency:
    """Runtime stand-in that raises a clear error instead of instantiating ``Any``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise AlpacaPerceptionError(
            "alpaca-py is not installed (add it to requirements.txt)."
        )


class _MissingAlpacaEnum:
    """Simple placeholder that exposes enum-like members without the Alpaca SDK."""

    Day = None
    PUT = None
    CALL = None


if TYPE_CHECKING:
    from alpaca.data.historical import (
        OptionHistoricalDataClient,
        StockHistoricalDataClient,
    )
    from alpaca.data.requests import (
        OptionChainRequest,
        OptionSnapshotRequest,
        StockBarsRequest,
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import ContractType
else:
    StockHistoricalDataClient = OptionHistoricalDataClient = TradingClient = (
        _MissingAlpacaDependency
    )
    StockBarsRequest = OptionChainRequest = OptionSnapshotRequest = (
        _MissingAlpacaDependency
    )
    TimeFrame = ContractType = _MissingAlpacaEnum

try:
    from alpaca.data.historical import (
        OptionHistoricalDataClient,
        StockHistoricalDataClient,
    )
    from alpaca.data.requests import (
        OptionChainRequest,
        OptionSnapshotRequest,
        StockBarsRequest,
    )
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import ContractType

    _ALPACA_SDK = True
except Exception:  # pragma: no cover - alpaca-py not installed  # noqa: BLE001
    _ALPACA_SDK = False
    # Keep install-time fallbacks available so missing SDK usage raises a clear
    # error instead of crashing with `typing.Any cannot be instantiated`.
    StockHistoricalDataClient = OptionHistoricalDataClient = TradingClient = (
        _MissingAlpacaDependency
    )
    StockBarsRequest = OptionChainRequest = OptionSnapshotRequest = (
        _MissingAlpacaDependency
    )
    TimeFrame = ContractType = _MissingAlpacaEnum


class AlpacaPerceptionError(RuntimeError):
    """Raised when the Alpaca SDK / credentials are unavailable."""


def _resolve_key_secret(credentials=None) -> tuple[str, str, bool]:
    """Return ``(api_key, secret_key, paper)`` for a request.

    Prefers a per-user ``AlpacaCredentials`` payload (``credentials``); falls back
    to the process env (.env) only when no payload is supplied (CLI / management
    commands). This is what lets two simultaneous dashboards keep fully separate
    broker sessions.
    """
    if credentials is not None:
        return credentials.api_key, credentials.secret_key, credentials.paper
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not api_key or not secret:
        raise AlpacaPerceptionError(
            "Missing ALPACA_API_KEY / ALPACA_API_SECRET_KEY in .env"
        )
    paper = os.environ.get("ALPACA_PAPER_MODE", "True").lower() in (
        "1", "true", "yes",
    )
    return api_key, secret, paper


# Per-credential-identity client cache. Clients are ALWAYS built from a single
# user's keys: bucket[profile-id-or-env]["stock"|"option"|"trading"]. This
# replaces the old process-wide singletons that leaked one user's account into
# another's ingestion path.
_client_cache: dict[str, dict[str, Any]] = {}
_client_lock = threading.Lock()


def _cache_key(credentials=None) -> str:
    if credentials is not None:
        return f"user:{credentials.profile_id}"
    return "legacy-env"


def _get_client(kind: str, credentials=None) -> Any:
    """Return a lazily-built client for ``kind``, isolated per credential identity."""
    _require_sdk()
    key, secret, paper = _resolve_key_secret(credentials)
    bucket_key = _cache_key(credentials)
    with _client_lock:
        bucket = _client_cache.setdefault(bucket_key, {})
        client = bucket.get(kind)
        if client is None:
            if kind == "stock":
                client = StockHistoricalDataClient(key, secret)
            elif kind == "option":
                client = OptionHistoricalDataClient(key, secret)
            elif kind == "trading":
                client = TradingClient(key, secret, paper=paper)
            else:
                raise ValueError(f"unknown client kind: {kind}")
            bucket[kind] = client
        return client


def stock_client(credentials=None) -> Any:
    return _get_client("stock", credentials)


def option_client(credentials=None) -> Any:
    return _get_client("option", credentials)


def trading_client(credentials=None) -> Any:
    return _get_client("trading", credentials)


def _require_sdk() -> None:
    if not _ALPACA_SDK:
        raise AlpacaPerceptionError(
            "alpaca-py is not installed (add it to requirements.txt)."
        )


def _to_plain(value: Any) -> Any:
    """Recursively normalize Alpaca pydantic/dataclass/numpy objects to JSON-safe."""
    from dataclasses import asdict, is_dataclass

    if value is None or isinstance(value, (str, bool)):
        return value
    # Normalize numpy scalars (from pandas rows / BarSet.df) to native Python
    # objects first - numpy ints/floats are not reliably JSON-serializable.
    if value.__class__.__module__.startswith("numpy") and hasattr(value, "item"):
        try:
            return _to_plain(value.item())
        except (ValueError, OverflowError, AttributeError):
            pass
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return _to_plain(asdict(value))
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Perception fetchers
# ---------------------------------------------------------------------------
def get_account_info(credentials=None) -> dict:
    """Current account state (equity, cash, buying power, P&L)."""
    return _to_plain(trading_client(credentials).get_account())


def get_positions(credentials=None) -> list[dict]:
    """Open positions as JSON-safe dicts."""
    return _to_plain(trading_client(credentials).get_all_positions())


def fetch_stock_bars(
    symbol: str,
    days: int = 10,
    timeframe: Any = None,
    limit: int = 50,
    credentials=None,
) -> list[dict]:
    """Recent historical bars for one symbol (defaults to daily)."""
    # Annotate as Any: at runtime TimeFrame is the _MissingAlpacaEnum fallback
    # whose .Day is typed `classproperty | None`, which Pylance would flag when
    # passing into StockBarsRequest's TimeFrame-typed `timeframe` param.
    tf: Any = timeframe if timeframe is not None else TimeFrame.Day
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=tf,
        start=datetime.now(timezone.utc) - timedelta(days=days),
        limit=limit,
    )
    response = stock_client(credentials).get_stock_bars(request)
    # alpaca-py's get_stock_bars returns a BarSet whose `.df` property is a
    # pandas DataFrame (indexed by (symbol, timestamp) when symbols are present).
    # `response[symbol]` on that object yields a list of Bar pydantic models,
    # not a DataFrame, so we normalize to the per-symbol frame instead. A
    # single-symbol frame may already drop the symbol index level, so only slice
    # by symbol when that level actually exists.
    frame = getattr(response, "df", response)
    index = getattr(frame, "index", None)
    if (
        index is not None
        and getattr(index, "nlevels", 1) > 1
        and symbol in index.get_level_values(0)
    ):
        frame = frame.xs(symbol, level=0)
    records: list[dict] = []
    for ts, row in frame.iterrows():
        records.append(
            {"timestamp": ts.isoformat(), **_to_plain(row.to_dict())}
        )
    return records


def fetch_option_chain(
    symbol: str,
    expiration_date: str | None = None,
    contract_type: str = "put",
    credentials=None,
) -> list[dict]:
    """Option chain for one underlying (defaults to puts for hedging)."""
    if ContractType is not None:
        contract = (
            ContractType.PUT if contract_type.lower() == "put" else ContractType.CALL
        )
    else:
        contract = None
    request = OptionChainRequest(
        underlying_symbol=symbol,
        type=contract,
        expiration_date=expiration_date,
    )
    chain = option_client(credentials).get_option_chain(request)
    contracts = getattr(chain, "option_contracts", chain)
    return _to_plain(contracts)


def fetch_option_snapshot(occ_symbol: str, credentials=None) -> dict:
    """Latest quote/bars for one or more option contracts."""
    request = OptionSnapshotRequest(symbol_or_symbols=occ_symbol)
    snapshots = option_client(credentials).get_option_snapshots(request)
    val = snapshots.get(occ_symbol) if hasattr(snapshots, "get") else snapshots
    return _to_plain(val)


def gather_perception(symbols: list[str] | None = None, credentials=None) -> dict:
    """Collect a snapshot of current perception: account + positions + bars.

    ``credentials`` (per-user Alpaca keys) isolates the underlying SDK clients to
    one account - never mixing simultaneous dashboard users.
    """
    account = get_account_info(credentials)
    positions = get_positions(credentials)
    tickers = list(symbols or [])
    if not tickers:
        tickers = [sym for p in positions if (sym := p.get("symbol"))]
    data: dict = {"account": account, "positions": positions, "symbols": tickers}
    for sym in tickers[:5]:
        try:
            data[sym] = {
                "bars": fetch_stock_bars(
                    sym, days=5, limit=20, credentials=credentials
                )
            }
        except Exception as exc:  # noqa: BLE001
            data[sym] = {"error": str(exc)}
    return data


# ---------------------------------------------------------------------------
# Ingestion pipeline: chunk -> embed -> index
# ---------------------------------------------------------------------------
def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """Split free text into overlapping chunks on whitespace boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            cut = text.rfind(" ", start, min(end + 1, n))
            if cut > start:
                end = cut
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def embed_text(text: str) -> list[float]:
    """Embed one string into a float vector using the configured embedder."""
    from agent.brain import _default_embedder

    return _default_embedder()(text)


def ingest_text(
    text: str,
    *,
    document_title: str = "",
    ticker_scope: str = "",
    sentiment_tag: str = "",
    source: str = "positions",
    published_at: datetime | None = None,
    max_chars: int = 800,
    overlap: int = 100,
    owner=None,
) -> list[str | None]:
    """Chunk, embed and store ``text``; returns the stored chunk UUIDs.

    ``owner`` (``UserProfile``) scopes the stored fact so later recall is kept
    per-user across concurrent dashboard sessions.
    """
    from agent.brain import store_semantic_knowledge

    ids: list[str | None] = []
    for chunk in chunk_text(text, max_chars=max_chars, overlap=overlap):
        doc_id = store_semantic_knowledge(
            chunk,
            document_title=document_title,
            ticker_scope=ticker_scope,
            sentiment_tag=sentiment_tag,
            source=source,
            published_at=published_at,
            owner=owner,
        )
        ids.append(doc_id)
    return ids


def ingest_perception(
    symbols: list[str] | None = None,
    credentials=None,
    owner=None,
) -> list[str | None]:
    """Pull current Alpaca perception and persist it as vectorized chunks.

    ``credentials`` (per-user Alpaca keys) isolates the fetch; ``owner`` scopes
    the stored memory to this user.
    """
    perception = gather_perception(symbols, credentials=credentials)
    ids: list[str | None] = []

    acc = perception.get("account") or {}
    ids += ingest_text(
        (
            f"Account: equity={acc.get('equity')} cash={acc.get('cash')} "
            f"buying_power={acc.get('buying_power')} "
            f"long_market_value={acc.get('long_market_value')} "
            f"unrealized_pl={acc.get('unrealized_pl')}"
        ),
        document_title="account state",
        source="account",
        owner=owner,
    )

    for pos in perception.get("positions") or []:
        sym = pos.get("symbol") or ""
        ids += ingest_text(
            (
                f"Position {sym}: qty={pos.get('qty')} "
                f"market_value={pos.get('market_value')} "
                f"unrealized_pl={pos.get('unrealized_pl')} "
                f"asset_class={pos.get('asset_class')}"
            ),
            document_title="position",
            ticker_scope=sym,
            source="positions",
            owner=owner,
        )

    for sym in (perception.get("symbols") or [])[:5]:
        bars = perception.get(sym, {}).get("bars") or []
        if not bars:
            continue
        closes = [b.get("close") for b in bars if b.get("close") is not None]
        published_at = None
        last = bars[-1].get("timestamp")
        if last:
            try:
                published_at = datetime.fromisoformat(last)
            except ValueError:
                published_at = None
        ids += ingest_text(
            (
                f"{sym} recent daily bars: latest close={closes[-1] if closes else None} "
                f"5-day close series={closes[-5:]}"
            ),
            document_title="recent stock bars",
            ticker_scope=sym,
            source="stock_bars",
            published_at=published_at,
            owner=owner,
        )
    return ids


