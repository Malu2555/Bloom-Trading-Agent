"""
Brain: the agent's autonomous cognitive engine.

The brain is the decision loop that turns raw Alpaca market data into a
disciplined, options-only trading decision. It:

1. **Perceives**   - gathers account/positions/history/clock and option chains
                     through ``agent.mcp_client`` (Alpaca MCP server).
2. **Remembers**   - persists each perceived fact as an embedding in a *dynamic
                     vector store*: **Chroma** for local dev, **pgvector**
                     (Neon/PostgreSQL) in production. It recalls this later via
                     ``store_semantic_knowledge`` / ``semantic_recall``.
3. **Thinks**      - assesses risk deterministically and, if configured, asks
                     the LLM to reason under an *options-only* system prompt that
                     enforces the Alpaca hackathon mandate: we trade and hedge
                     with **options** (protective puts, covered calls,
                     cash-secured puts, option spreads) - never naked directional
                     bets outside a documented strategy.
4. **Acts**        - in DRY_RUN (default) it simulates; only with ``dry_run=False``
                     does it submit option orders through the MCP client.
5. **Persists**    - writes Decision / OrderRecord / Hedge / PortfolioSnapshot /
                     EmbeddedRiskContext rows for audit, P&L and recall.

Strategies encoded in the options-only prompt (mirroring the models' enums and
the rest of the codebase):

- ``protective_put``  - the core hedge: buy a put under a long position.
- ``covered_call``    - generate income against a long position.
- ``cash_secured_put``- collect premium while willing to buy stock.
- ``spread``          - vertical spreads to cap cost / de-risk.
- ``unwind``          - reduce/exit risk (option side first).
- ``close_hedge``     - take profits / remove an unnecessary hedge.
- ``emergency_flat``  - protect capital when drawdown hits the alarm.

Every step is logged through the module ``logger``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, TypedDict

from pydantic import SecretStr

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_bool(key: str, default: str = "True") -> bool:
    return os.environ.get(key, default).lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Inference providers (OpenAI-compatible endpoints)
# ---------------------------------------------------------------------------
# Fireworks is the primary; Featherless is the automatic fallback if Fireworks
# is unreachable or its key is missing. Both speak the OpenAI wire format, so a
# single client library (langchain_openai) talks to whichever is live.
_INFERENCE_PROVIDERS = {
    "featherless": {
        "env_key": "FEATHERLESS_API_KEY",
        "base_url": "https://api.featherless.ai/v1",
        "llm_model": "meta-llama/Llama-3.1-70B-Instruct",
        "embed_model": "Qwen/Qwen3-Embedding-8B",
    },
    "fireworks": {
        "env_key": "FIREWORKS_API_KEY",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "llm_model": "accounts/fireworks/models/deepseek-v4-flash-0731",
        "embed_model": "accounts/fireworks/models/qwen3-embedding-8b",
    },
}
# Preference order: primary first. Only entries with a configured key are used.
_INFERENCE_PREFERENCE = ("fireworks", "featherless")


def _resolve_inference_candidates() -> list[tuple[str, str, str]]:
    """Return ``(provider, api_key, base_url)`` candidates in preference order.

    Only providers with a configured API key are included. A shared override
    (``AGENT_LLM_BASE_URL`` / ``EMBEDDER_BASE_URL``) or a per-provider base URL
    (``FEATHERLESS_BASE_URL`` / ``FIREWORKS_BASE_URL``) wins over the defaults.
    """
    override = (
        os.environ.get("AGENT_LLM_BASE_URL", "")
        or os.environ.get("EMBEDDER_BASE_URL", "")
    ).strip()
    candidates: list[tuple[str, str, str]] = []
    for provider in _INFERENCE_PREFERENCE:
        spec = _INFERENCE_PROVIDERS[provider]
        api_key = os.environ.get(spec["env_key"], "").strip()
        if not api_key:
            continue
        base_url = (
            override
            or os.environ.get(f"{provider.upper()}_BASE_URL", "").strip()
            or spec["base_url"]
        )
        candidates.append((provider, api_key, base_url))
    return candidates


def _inference_model(provider: str, kind: str) -> str:
    """Resolve the model name for ``provider`` and kind (``llm``/``embed``).

    Priority: explicit per-provider override, generic env var, provider default.
    """
    spec = _INFERENCE_PROVIDERS[provider]
    key = "LLM" if kind == "llm" else "EMBEDDING"
    env_var = f"{provider.upper()}_{key}_MODEL"
    generic_var = "AGENT_LLM_MODEL" if kind == "llm" else "EMBEDDING_MODEL"
    return (
        os.environ.get(env_var, "").strip()
        or os.environ.get(generic_var, "").strip()
        or spec["llm_model" if kind == "llm" else "embed_model"]
    )


@dataclass
class AgentConfig:
    """Runtime configuration for the agent, loaded from Django settings / env."""

    dry_run: bool = True
    heuristic_only: bool = True
    min_equity_for_hedge: Decimal = Decimal("1000")
    max_drawdown_ratio: Decimal = Decimal("0.10")
    target_coverage_ratio: Decimal = Decimal("1.0")
    protection_floor_ratio: Decimal = Decimal("0.90")
    premium_budget_ratio: Decimal = Decimal("0.02")
    hedge_days_to_expiry: int = 30
    market_close_skip: bool = True
    min_position_value_for_hedge: Decimal = Decimal("2000")
    # Autonomous position-opening controls.
    max_position_pct: Decimal = Decimal("0.10")  # max % of equity per stock position
    max_positions: int = 5  # max concurrent stock positions
    min_cash_reserve: Decimal = Decimal("5000")  # keep this much cash on hand
    auto_open_positions: bool = False  # master switch for autonomous entry

    llm_provider: str = "fireworks"
    llm_model: str = "accounts/fireworks/models/deepseek-v4-flash-0731"
    llm_api_key: str = ""
    llm_base_url: str = ""
    mcp_command: str = ""
    toolsets: str = "account,trading,assets,options-data,stock-data"

    @classmethod
    def from_settings(cls, overrides: dict | None = None) -> "AgentConfig":
        """Build from ``settings.AGENT_CONFIG`` merged with per-call overrides."""
        try:
            from django.conf import settings

            raw = dict(getattr(settings, "AGENT_CONFIG", {}))
        except Exception:  # pragma: no cover - settings unavailable  # noqa: BLE001
            raw = {}
        if overrides:
            raw.update(overrides)

        def _dec(key: str, default: Decimal) -> Decimal:
            try:
                return Decimal(str(raw.get(key, default)))
            except (InvalidOperation, TypeError, ValueError):
                return default

        cfg = cls(
            dry_run=bool(raw.get("dry_run", True)),
            heuristic_only=bool(raw.get("heuristic_only", True)),
            min_equity_for_hedge=_dec("min_equity_for_hedge", Decimal("1000")),
            max_drawdown_ratio=_dec("max_drawdown_ratio", Decimal("0.10")),
            target_coverage_ratio=_dec("target_coverage_ratio", Decimal("1.0")),
            protection_floor_ratio=_dec("protection_floor_ratio", Decimal("0.90")),
            premium_budget_ratio=_dec("premium_budget_ratio", Decimal("0.02")),
            hedge_days_to_expiry=int(raw.get("hedge_days_to_expiry", 30)),
            market_close_skip=bool(raw.get("market_close_skip", True)),
            min_position_value_for_hedge=_dec(
                "min_position_value_for_hedge", Decimal("2000")
            ),
            max_position_pct=_dec("max_position_pct", Decimal("0.10")),
            max_positions=int(raw.get("max_positions", 5)),
            min_cash_reserve=_dec("min_cash_reserve", Decimal("5000")),
            auto_open_positions=bool(raw.get("auto_open_positions", False)),
            llm_provider=str(raw.get("llm_provider", "fireworks")),
            llm_model=str(raw.get("llm_model", "accounts/fireworks/models/deepseek-v4-flash-0731")),
            llm_api_key=str(raw.get("llm_api_key", "")),
            llm_base_url=str(raw.get("llm_base_url", "")),
            mcp_command=str(raw.get("mcp_command", "")),
            toolsets=str(raw.get("toolsets", "account,trading,assets,options-data,stock-data")),
        )
        # Environment overrides always win for secrets.
        cfg.llm_api_key = (
            cfg.llm_api_key
            or os.environ.get("FIREWORKS_API_KEY", "")
            or os.environ.get("FEATHERLESS_API_KEY", "")
        )
        cfg.llm_base_url = cfg.llm_base_url or os.environ.get("AGENT_LLM_BASE_URL", "")
        return cfg

    # Map of UserProfile agent fields -> AgentConfig key names.
    PROFILE_FIELD_MAP = {
        "agent_dry_run": "dry_run",
        "agent_auto_open": "auto_open_positions",
        "agent_heuristic_only": "heuristic_only",
        "agent_max_position_pct": "max_position_pct",
        "agent_max_positions": "max_positions",
        "agent_min_cash_reserve": "min_cash_reserve",
    }

    @classmethod
    def profile_overrides(cls, owner) -> dict:
        """Return a config override dict derived from a ``UserProfile``.

        Only non-``None`` profile fields are included, so a profile that has never
        been configured still falls back to env/settings defaults. Keys use the
        ``AgentConfig`` field names and can be passed straight to
        ``from_settings``.
        """
        if owner is None:
            return {}
        out: dict = {}
        for field, key in cls.PROFILE_FIELD_MAP.items():
            val = getattr(owner, field, None)
            if val is None:
                continue
            if key in ("max_position_pct", "min_cash_reserve"):
                val = str(val)
            out[key] = val
        return out

    @classmethod
    def effective_for(cls, owner):
        """Return the effective agent settings for a user (env + profile overlay).

        Produces the profile-shaped dict the UI surfaces on ``GET /agent/config``:
        env/settings defaults, overridden by any concrete values the profile has.
        """
        base = cls.from_settings()
        values = {
            "dry_run": base.dry_run,
            "auto_open_positions": base.auto_open_positions,
            "heuristic_only": base.heuristic_only,
            "max_position_pct": base.max_position_pct,
            "max_positions": base.max_positions,
            "min_cash_reserve": base.min_cash_reserve,
            "profile_override": False,
        }
        if owner is not None:
            overlay = cls.profile_overrides(owner)
            if overlay:
                values.update(overlay)
                values["profile_override"] = True
        values["max_position_pct"] = float(values["max_position_pct"])
        values["min_cash_reserve"] = float(values["min_cash_reserve"])
        return values


def _vector_dimensions() -> int:
    try:
        return int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))
    except (TypeError, ValueError):
        return 1536


VECTOR_DIMENSIONS = _vector_dimensions()

# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------
#
# Embedders are composed into a single ``_default_embedder`` with a strict order:
#
# 1. Remote (production): Fireworks / OpenAI-compatible embeddings via
#    ``accounts/fireworks/models/qwen3-embedding-8b`` (Featherless's
#    Qwen/Qwen3-Embedding-8B as fallback). Used when an API key is present and
#    ``EMBEDDING_FORCE_LOCAL`` is unset.
# 2. Local sentence-transformers (dev/offline): ``all-MiniLM-L6-v2`` via the
#    ``sentence-transformers`` package (see requirements.txt). Real semantic
#    vectors computed on CPU - no network or API key required. Forced on when
#    ``EMBEDDING_FORCE_LOCAL`` is set, and used automatically when remote fails.
# 3. Deterministic hashed bag-of-tokens embedder: last resort that always returns
#    a fixed-dimension (``EMBEDDING_DIMENSIONS``) unit vector so the pipeline
#    never hard-fails.


def _build_remote_embedder() -> Callable[[str], list[float]] | None:
    """Build a remote embedder from the first available provider.

    Tries providers in preference order (Fireworks first, Featherless as
    backstop). Candidates that fail to instantiate or warm up are skipped so a
    down provider falls through to the next one. Returns ``None`` when no remote
    provider is usable (caller falls back to the local embedder).
    """
    try:
        from langchain_openai import OpenAIEmbeddings
    except Exception:  # pragma: no cover - langchain missing  # noqa: BLE001
        return None
    try:
        from pydantic import SecretStr
    except Exception:  # pragma: no cover - pydantic missing  # noqa: BLE001
        return None

    for provider, api_key, base_url in _resolve_inference_candidates():
        model = _inference_model(provider, "embed")
        try:
            emb = OpenAIEmbeddings(
                api_key=SecretStr(api_key), base_url=base_url, model=model
            )
            probe = emb.embed_query("warmup")
            if not isinstance(probe, list) or len(probe) == 0:
                raise ValueError("empty embedding probe")
            logger.info("Remote embedder ready: %s (%s) at %s", model, provider, base_url)
            return emb.embed_query
        except Exception as exc:  # pragma: no cover - network/keys unreliable  # noqa: BLE001
            logger.warning(
                "Embedder via %s unavailable (%s); trying next provider.",
                provider,
                exc,
            )
    return None


def _build_sentence_embedder() -> Callable[[str], list[float]] | None:
    """Build a local ``sentence-transformers`` embedder (CPU, offline).

    Returns ``None`` when the package isn't installed (it's optional in
    requirements.txt) so the caller falls through to the hash embedder. Warmup
    failures (e.g. broken torch install) are caught and logged, never raised.
    """
    model_name = os.environ.get("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - package optional/not installed  # noqa: BLE001
        logger.warning("sentence-transformers not installed; skipping local embedder.")
        return None
    try:
        model = SentenceTransformer(model_name)
        # Version-robust: newer sentence-transformers renamed this method.
        dim_fn = getattr(model, "get_embedding_dimension", None) or (
            model.get_sentence_embedding_dimension
        )
        _raw_dims = dim_fn()
        dims = int(_raw_dims) if _raw_dims is not None else 0
        logger.info("Local sentence embedder ready: %s (%d-dim)", model_name, dims)

        def _embed(text: str) -> list[float]:
            vec = model.encode(text, normalize_embeddings=True)
            return [float(x) for x in vec]

        return _embed
    except Exception as exc:  # pragma: no cover - torch/model load unreliable  # noqa: BLE001
        logger.warning("Local sentence embedder unavailable (%s).", exc)
        return None


def _build_local_embedder() -> Callable[[str], list[float]]:
    """Deterministic hashed bag-of-tokens embedder (fixed dimension, unit vector)."""
    dims = VECTOR_DIMENSIONS

    def _hash_token(token: str, salt: str) -> int:
        return int(hashlib.md5((salt + token).encode("utf-8")).hexdigest(), 16)

    def _embed(text: str) -> list[float]:
        vector = [0.0] * dims
        if not text:
            return vector
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            idx = _hash_token(token, "") % dims
            sign = 1.0 if (_hash_token(token, "s") % 2) == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [round(v / norm, 9) for v in vector]

    return _embed


_remote_embedder: Callable[[str], list[float]] | None = None
_sentence_embedder: Callable[[str], list[float]] | None = None
_hash_embedder: Callable[[str], list[float]] | None = None
_remote_attempted = False


def _default_embedder() -> Callable[[str], list[float]]:
    """Return the process-wide embedder.

    Resolution order:
      1. Remote (Fireworks, Featherless fallback) -- unless ``EMBEDDING_FORCE_LOCAL``
         is set, which is how local dev forces offline embeddings.
      2. Local sentence-transformers -- used offline / forced-in-dev / when remote
         is unavailable.
      3. Deterministic hashed bag-of-tokens -- last resort, always works.

    The remote attempt is made at most once per process; a failed probe permanently
    falls through to local instead of hammering the API on every call.
    """
    global _remote_embedder, _sentence_embedder, _hash_embedder, _remote_attempted

    force_local = os.environ.get("EMBEDDING_FORCE_LOCAL", "").lower() in ("1", "true")
    if not force_local and _remote_embedder is None and not _remote_attempted:
        _remote_attempted = True
        _remote_embedder = _build_remote_embedder()
    if not force_local and _remote_embedder is not None:
        return _remote_embedder

    if _sentence_embedder is None:
        _sentence_embedder = _build_sentence_embedder()
    if _sentence_embedder is not None:
        return _sentence_embedder

    if _hash_embedder is None:
        _hash_embedder = _build_local_embedder()
    return _hash_embedder

# ---------------------------------------------------------------------------
# Options-only system prompt (Alpaca hackathon core mandate)
# ---------------------------------------------------------------------------

OPTIONS_ONLY_SYSTEM_PROMPT = """You are the options-only trading cognitive engine for the Alpaca Builder
Hackathon paper-trading agent. You are an AUTONOMOUS trader: you both open
positions and manage them with listed equity OPTIONS. Your account starts with
cash and NO positions, so you must be able to deploy capital intelligently, not
just hedge what already exists. You do NOT short naked stock to speculate, and
you do NOT trade crypto or non-equity instruments.

MANDATE (non-negotiable):
1. DEPLOY CAPITAL. When you hold cash above the minimum reserve and the market
   presents a high-conviction long idea, you may BUY STOCK to open a position
   (sized to the max_position_pct budget, never exceeding max_positions total).
   Alternatively, open a cash-secured put to acquire stock at a discount and
   collect premium while you wait.
2. RISK FIRST. Protecting existing long positions from drawdown is your core
   job. When a held position is at risk, your default hedge is the PROTECTIVE
   PUT (buy a put with a strike at or near the protection floor). Generate
   income with covered calls on positions you are willing to hold.
3. CONSTRAINTS. Respect premium/risk budgets. Never exceed max_positions open
   stock positions. Never draw cash below the min_cash_reserve. Never place a
   position larger than max_position_pct of total equity. Never invent an OCC
   symbol. Never recommend a market order for a symbol you cannot source from
   the option chain.
4. PAPER SAFETY. You operate on a paper account. Prefer defined-risk, debit or
   cash-collateralize structures.

STRATEGIES YOU MAY USE (in priority order relevant to the current book):
- buy_stock: open/scale a cash long on a high-conviction idea. Only when there
  is room under max_positions and the notional stays under max_position_pct of
  equity (use qty ~ floor(equity * max_position_pct / spot) or a notional order).
- cash_secured_put: collect premium while willing to acquire the stock at the
  strike; cash secured. Good for neutral/bearish entry where you want a lower
  buy price.
- covered_call: income against a long position. Sell an out-of-the-money call,
  aiming for premium while keeping upside room. Only for positions you are fine
  holding.
- protective_put: core hedge. Buy an American put, strike ~ floor price, expiry
  ~ hedge_days_to_expiry, sized to the number of 100-share contracts that cover
  the long position. Used when coverage is below target or drawdown is rising.
- spread: bull/bear vertical spread to lower the net debit of a long option or
  cap the risk of a short option.
- unwind: reduce total risk. Close the most expensive/low-value hedge first, or
  roll a near-expiry hedge instead of opening a new one.
- close_hedge: take profits on / remove a hedge once the underlying has moved
  away from the floor and coverage is back at or above target.
- emergency_flat: ONLY on catastrophic drawdown signal (drawdown >= max allowed).
  Protect capital now; flatten the hedged book and cancel rests.

DECISION OUTPUT - respond ONLY with JSON (no prose, no markdown fences) shaped:
{{
  "action": "hold" | "buy_stock" | "hedge" | "covered_call" | "cash_secured_put"
            | "spread" | "unwind" | "close_hedge" | "emergency",
  "confidence": <0.0 to 1.0>,
  "justification": "<why, with figures>",
  "hedge": {{ "underlying": "<TICKER or empty>",
             "strategy": "protective_put",
             "floor_ratio": <optional number>,
             "days_to_expiry": <optional integer> }} | null,
  "trade": {{ "strategy": "buy_stock" | "cash_secured_put",
              "symbol": "<TICKER>",
              "side": "buy" | "sell",
              "qty": <integer or null>,
              "notional": <dollar amount or null>,
              "strike": <optional number>,
              "expiry": "<optional YYYY-MM-DD>",
              "order_type": "market" | "limit",
              "limit_price": <optional number> }} | null
}}

Rules of thumb: if equity < min_equity_for_hedge -> HOLD and preserve cash.
If no open positions and cash is comfortably above min_cash_reserve and
auto_open_positions is enabled -> BUY_STOCK (or open a cash_secured_put).
If coverage < target_coverage_ratio and a long position exceeds the min size ->
HEDGE (protective_put). If drawdown >= max_drawdown_ratio -> EMERGENCY. If
already covered and the underlying recovered -> CLOSE_HEDGE. Otherwise HOLD."""

# ---------------------------------------------------------------------------
# Dynamic vector store: Chroma (local) <-> pgvector (Neon production)
# ---------------------------------------------------------------------------
#
# The architecture selects the backend at runtime:
#
#   pgvector  - used on PostgreSQL (production/Neon, when ``DATABASE_URL`` is
#               present). Rows live in ``EmbeddedRiskContext.embedding_vector``
#               and search is done with the ``<=>`` cosine-distance operator.
#   chroma    - used locally (SQLite dev/test). A persistent Chroma collection
#               under ``VECTOR_STORE_DIR`` holds the embeddings.
#
# ``store_semantic_knowledge`` / ``semantic_recall`` are the only two entry
# points and transparently dispatch to whichever backend is active.


def _pgvector_available() -> bool:
    try:
        from agent.models import _pgvector_enabled

        return bool(_pgvector_enabled())
    except Exception:  # pragma: no cover  # noqa: BLE001
        return False


def _chroma_path() -> str:
    override = os.environ.get("VECTOR_STORE_DIR", "").strip()
    if override:
        return override
    # Default under backend/vector_store (persistent across runs in dev).
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "vector_store")


_chroma_collection = None


def _get_chroma_collection():
    """Lazily build + return the persistent Chroma collection for local memory."""
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection
    import chromadb

    path = _chroma_path()
    os.makedirs(path, exist_ok=True)
    client = chromadb.PersistentClient(path=path)
    # Chroma persists/validates the embedding function by its ``name()``. Wrap our
    # embedder in an ``EmbeddingFunction`` instance so add/query stay dimension-
    # consistent and ``get_or_create`` doesn't reject a bare callable.
    from chromadb.utils import embedding_functions

    class _AppEmbedder(embedding_functions.EmbeddingFunction):
        def __call__(self, input):
            if isinstance(input, str):
                input = [input]
            func = _default_embedder()
            return [func(t) for t in input]

        def name(self) -> str:
            return "agent_semantic_memory"

    try:
        collection = client.get_or_create_collection(
            name="agent_semantic_memory",
            embedding_function=_AppEmbedder(),
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:  # pragma: no cover - pre-existing mismatched EF  # noqa: BLE001
        # A collection already exists with a different persisted embedding config.
        # We always pass explicit embeddings/query_embeddings, so reuse it as-is.
        logger.info("Reusing existing Chroma collection (ignored EF mismatch: %s).", exc)
        collection = client.get_collection(name="agent_semantic_memory")

    _chroma_collection = collection
    return collection


def _make_metadata(
    *,
    document_title: str = "",
    ticker_scope: str = "",
    sentiment_tag: str = "",
    source: str = "positions",
    published_at: datetime | None = None,
    extra: dict | None = None,
) -> dict:
    meta: dict[str, Any] = {
        "document_title": document_title or "",
        "ticker_scope": ticker_scope or "",
        "sentiment_tag": sentiment_tag or "",
        "source": source or "positions",
    }
    if published_at is not None:
        meta["published_at"] = published_at.isoformat()
    if extra:
        meta.update(extra)
    return meta


def store_semantic_knowledge(
    text: str,
    *,
    document_title: str = "",
    ticker_scope: str = "",
    sentiment_tag: str = "",
    source: str = "positions",
    published_at: datetime | None = None,
    embedding: list[float] | None = None,
    metadata: dict | None = None,
    owner=None,
) -> str | None:
    """Embed + persist ONE text fact into the active vector store.

    Local (Chroma): adds to the persistent ``agent_semantic_memory`` collection,
    returns a uuid string id. Production (pgvector): creates an
    ``EmbeddedRiskContext`` row with ``embedding_vector`` set, returns its id.

    ``owner`` (``UserProfile``) scopes the stored fact to its owner so semantic
    memory is recalled per-user (no cross-tenant bleed).
    """
    text = (text or "").strip()
    if not text:
        return None
    if embedding is None:
        embedding = _default_embedder()(text)
    meta = metadata or _make_metadata(
        document_title=document_title,
        ticker_scope=ticker_scope,
        sentiment_tag=sentiment_tag,
        source=source,
        published_at=published_at,
        extra={"owner_id": owner.id} if owner is not None else None,
    )

    if _pgvector_available():
        try:
            from agent.models import EmbeddedRiskContext

            obj = EmbeddedRiskContext.objects.create(
                document_title=document_title or "",
                raw_text_chunk=text,
                embedding_vector=embedding,
                ticker_scope=ticker_scope or "",
                sentiment_tag=sentiment_tag or "",
                source=source or "positions",
                published_at=published_at,
                metadata=meta,
                owner_id=(owner.id if owner is not None else None),
            )
            return str(obj.id)
        except Exception as exc:  # pragma: no cover - defensive  # noqa: BLE001
            logger.warning("pgvector write failed (%s); falling back to Chroma.", exc)
            # fall through to Chroma so memory is not lost

    collection = _get_chroma_collection()
    doc_id = str(uuid.uuid4())
    collection.add(
        ids=[doc_id],
        documents=[text],
        embeddings=[embedding],
        metadatas=[meta],
    )
    return doc_id


def semantic_recall(
    query: str,
    k: int = 5,
    *,
    ticker_scope: str | None = None,
    owner=None,
) -> list[dict]:
    """Semantic nearest-neighbor recall against the active vector store.

    Returns a list of ``{"id", "document", "distance", "metadata"}`` dicts,
    closest first. ``ticker_scope`` optionally narrows the search to one symbol;
    ``owner`` (``UserProfile``) narrows it to that user's own stored memory.
    """
    if not (query or "").strip():
        return []
    query_embedding = _default_embedder()(query.strip())
    results: list[dict] = []

    if _pgvector_available():
        from django.db.models.expressions import RawSQL

        from agent.models import EmbeddedRiskContext

        qs = EmbeddedRiskContext.objects.all()
        if owner is not None:
            qs = qs.filter(owner_id=owner.id)
        if ticker_scope:
            qs = qs.filter(ticker_scope=ticker_scope)
        vec_literal = str(query_embedding)
        rows = list(
            qs.annotate(
                _distance=RawSQL("embedding_vector <=> %s::vector", (vec_literal,))
            ).order_by("_distance")[:k]
        )
        for r in rows:
            results.append(
                {
                    "id": str(r.id),
                    "document": r.raw_text_chunk,
                    "distance": float(getattr(r, "_distance") or 0.0),
                    "metadata": {
                        "document_title": r.document_title,
                        "ticker_scope": r.ticker_scope,
                        "sentiment_tag": r.sentiment_tag,
                        "source": r.source,
                        "published_at": r.published_at.isoformat()
                        if r.published_at
                        else None,
                    },
                }
            )
        return results

    collection = _get_chroma_collection()
    # Chroma ``where`` needs exact-match filters. Combine owner + ticker so recall
    # never crosses tenant lines. Existing (pre-isolation) vectors have no
    # ``owner_id`` metadata and are excluded when an owner filter is present.
    where: Any = None
    filters: list[dict] = []
    if owner is not None:
        filters.append({"owner_id": {"$eq": owner.id}})
    if ticker_scope:
        filters.append({"ticker_scope": {"$eq": ticker_scope}})
    if filters:
        where = {"$and": filters} if len(filters) > 1 else filters[0]
    try:
        res = collection.query(
            query_embeddings=[query_embedding],
            n_results=int(k),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:  # pragma: no cover - query may be empty  # noqa: BLE001
        logger.debug("Chroma query failed (%s); returning empty recall.", exc)
        return results
    ids = (res.get("ids") or [[]])[0] or []
    docs = (res.get("documents") or [[]])[0] or []
    metas = (res.get("metadatas") or [[]])[0] or []
    dists = (res.get("distances") or [[]])[0] or []
    for i, doc_id in enumerate(ids):
        results.append(
            {
                "id": str(doc_id),
                "document": docs[i] if i < len(docs) else "",
                "distance": float(dists[i]) if i < len(dists) else float("nan"),
                "metadata": metas[i] if i < len(metas) else {},
            }
        )
    return results


class SemanticMemory:
    """Object-oriented facade over the dynamic vector store.

    ``add(text, **meta)`` -> id; ``search(query, k, ticker_scope)`` -> dicts.
    Dispatch to pgvector or Chroma is handled internally; identical callers work
    identically across local dev and Neon production.
    """

    def add(
        self,
        text: str,
        *,
        document_title: str = "",
        ticker_scope: str = "",
        sentiment_tag: str = "",
        source: str = "positions",
        published_at: datetime | None = None,
        owner=None,
    ) -> str | None:
        return store_semantic_knowledge(
            text,
            document_title=document_title,
            ticker_scope=ticker_scope,
            sentiment_tag=sentiment_tag,
            source=source,
            published_at=published_at,
            owner=owner,
        )

    def search(
        self,
        query: str,
        k: int = 5,
        *,
        ticker_scope: str | None = None,
        owner=None,
    ) -> list[dict]:
        return semantic_recall(query, k=k, ticker_scope=ticker_scope, owner=owner)

# ---------------------------------------------------------------------------
# Risk assessment and hedging math
# ---------------------------------------------------------------------------


def _dec(value: Any, default: str | int | float = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


def compute_protective_put(
    shares: Any,
    spot: Any,
    floor_ratio: Any,
    cfg: AgentConfig,
) -> dict:
    """Design a protective put hedge for a long position.

    Returns ``contracts``, ``target_strike``, ``floor`` (dollar floor level),
    ``expiry`` (ISO date) and ``premium_budget`` for the whole hedge.
    """
    shares = _dec(shares)
    spot = _dec(spot)
    floor_ratio = _dec(floor_ratio, "0.90")
    if shares <= 0 or spot <= 0:
        raise InvalidOperation("protective put requires positive shares and spot")

    contracts = int((shares / Decimal(100)).to_integral_value(rounding=ROUND_DOWN))
    floor = (spot * floor_ratio).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    # Round the strike DOWN to a whole $10 bucket so the put is still in the money
    # at our floor (gap hedging - never buy a strike above the floor).
    target_strike = (floor / Decimal(10)).to_integral_value(
        rounding=ROUND_DOWN
    ) * Decimal(10)
    expiry = (date.today() + timedelta(days=int(cfg.hedge_days_to_expiry))).isoformat()
    premium_budget = (spot * contracts * 100 * cfg.premium_budget_ratio).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    return {
        "contracts": contracts,
        "target_strike": target_strike,
        "floor": floor,
        "expiry": expiry,
        "premium_budget": premium_budget,
        "underlying_price": spot,
        "coverage": cfg.target_coverage_ratio,
    }


def select_protective_put_contract(
    contracts: Sequence[dict] | None,
    target_strike: Any,
    expiry: str | None = None,
) -> dict | None:
    """Pick the put contract nearest the target strike from an option chain.

    ``contracts`` is a list of dicts (each with at least ``occ_symbol`` and a
    ``strike``/``expiration_date``). Returns the best match or ``None``.
    """
    target = _dec(target_strike)
    best: tuple[Decimal, dict] | None = None
    for contract in contracts or []:
        if not isinstance(contract, dict):
            continue
        strike = contract.get("strike", contract.get("strike_price"))
        if strike in (None, "", 0):
            continue
        try:
            strike_dec = _dec(strike)
        except Exception:  # noqa: BLE001
            continue
        if expiry:
            exp = contract.get(
                "expiration_date", contract.get("expiry", contract.get("expiration"))
            )
            if exp and str(exp) != str(expiry):
                continue
        diff = abs(strike_dec - target)
        if best is None or diff < best[0]:
            best = (diff, contract)
    return best[1] if best else None

def assess_risk(
    account: dict | None,
    positions: Sequence[dict] | None,
    cfg: AgentConfig,
) -> dict:
    """Deterministic risk snapshot: exposure, coverage, drawdown, hedge trigger.

    Positions are long equity positions (asset_class 'stock'/empty). Each long
    position's uncovered market value contributes to ``at_risk_exposure``.
    """
    account = account or {}
    positions = positions or []
    equity = _dec(account.get("equity") or account.get("portfolio_value"), "0")
    if equity <= 0:
        equity = _dec(0)
    day_pnl = _dec(account.get("day_pnl") or 0)
    cash = _dec(account.get("cash") or 0)
    long_market_value = _dec(account.get("long_market_value") or 0)

    longs: list[dict] = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        asset = str(pos.get("asset_class") or "").lower()
        if asset in ("us_option", "option"):
            continue
        side = str(pos.get("side") or "long").lower()
        if side in ("long", ""):
            longs.append(pos)

    at_risk = sum(_dec(p.get("market_value") or p.get("long_market_value")) for p in longs)
    unrealized = _dec(account.get("unrealized_pl") or 0)
    cost_basis = equity - unrealized if unrealized else long_market_value
    drawdown = Decimal("0")
    if cost_basis > 0 and unrealized < 0:
        drawdown = (abs(unrealized) / cost_basis).quantize(Decimal("0.0001"))

    # Hedge coverage: value the active hedge book against equity (best effort).
    hedge_mv = _dec(0)
    try:
        from agent.models import Hedge

        for h in Hedge.objects.filter(status__in=("open", "pending"))[:200]:
            hedge_mv += _dec(h.protection_floor) * _dec(h.contracts) * 100
    except Exception:  # pragma: no cover - model/table unavailable  # noqa: BLE001
        pass

    coverage = Decimal("0")
    if equity > 0:
        coverage = min(Decimal("1"), hedge_mv / equity)

    needs_hedge = (
        equity >= cfg.min_equity_for_hedge
        and at_risk > cfg.min_position_value_for_hedge
        and coverage < cfg.target_coverage_ratio
    )
    emergency = drawdown >= cfg.max_drawdown_ratio
    if equity < cfg.min_equity_for_hedge or at_risk <= cfg.min_position_value_for_hedge:
        needs_hedge = False

    return {
        "equity": equity,
        "cash": cash,
        "long_market_value": long_market_value,
        "day_pnl": day_pnl,
        "unrealized_pl": unrealized,
        "at_risk_exposure": at_risk,
        "hedge_coverage_ratio": coverage,
        "drawdown_ratio": drawdown,
        "num_long_positions": len(longs),
        "uncovered_positions": [
            {"symbol": p.get("symbol", ""), "market_value": _dec(p.get("market_value"))}
            for p in longs
        ],
        "needs_hedge": bool(needs_hedge),
        "emergency": bool(emergency),
        "target_coverage_ratio": cfg.target_coverage_ratio,
        "max_drawdown_ratio": cfg.max_drawdown_ratio,
        "protection_floor_ratio": cfg.protection_floor_ratio,
    }

# ---------------------------------------------------------------------------
# Decision paths (heuristic + optional LLM)
# ---------------------------------------------------------------------------


def _heuristic_decision(risk: dict, cfg: AgentConfig) -> dict:
    """Deterministic fallback when no LLM is configured or it is unavailable."""
    if risk.get("emergency"):
        return {
            "action": "emergency",
            "confidence": 0.99,
            "justification": (
                f"Drawdown {float(risk['drawdown_ratio']):%} >= max "
                f"{float(cfg.max_drawdown_ratio):%}; protect capital now."
            ),
            "hedge": None,
        }
    if risk.get("needs_hedge"):
        pos = (risk.get("uncovered_positions") or [{}])[0]
        return {
            "action": "hedge",
            "confidence": 0.9,
            "justification": (
                f"Coverage {float(risk['hedge_coverage_ratio']):%} < target "
                f"{float(cfg.target_coverage_ratio):%} on {pos.get('symbol', '')}; "
                f"buy a protective put at floor {float(risk['protection_floor_ratio']):%}."
            ),
            "hedge": {
                "underlying": pos.get("symbol", ""),
                "strategy": "protective_put",
                "floor_ratio": float(risk["protection_floor_ratio"]),
                "days_to_expiry": cfg.hedge_days_to_expiry,
            },
        }

    # Autonomous entry: deploy idle cash when enabled and the book is empty/light.
    if cfg.auto_open_positions:
        equity = _dec(risk.get("equity") or 0)
        cash = _dec(risk.get("cash") or 0)
        num_long = int(risk.get("num_long_positions") or 0)
        if num_long < cfg.max_positions and cash > cfg.min_cash_reserve:
            budget = equity * cfg.max_position_pct
            trade = {
                "strategy": "buy_stock",
                "symbol": "SPY",
                "side": "buy",
                "qty": None,
                "notional": str(budget),
                "order_type": "market",
                "limit_price": None,
                "_equity": str(equity),
            }
            return {
                "action": "buy_stock",
                "confidence": 0.65,
                "justification": (
                    f"Room to invest: {num_long}/{cfg.max_positions} positions, "
                    f"cash {float(cash):.2f} above reserve {float(cfg.min_cash_reserve):.2f}; "
                    f"buy ~{float(budget):.2f} of SPY to deploy capital."
                ),
                "hedge": None,
                "trade": trade,
            }

    return {
        "action": "hold",
        "confidence": 0.8,
        "justification": "No hedge needed and no drawdown alarm; hold positions.",
        "hedge": None,
    }


def _new_llm(provider: str, api_key: str, base_url: str):
    """Build a ``ChatOpenAI`` client against a specific provider endpoint."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=_inference_model(provider, "llm"),
        api_key=SecretStr(api_key),
        base_url=base_url,
        temperature=0.2,
        max_retries=1,
    )


def _build_llm():
    """Build a ``ChatOpenAI`` client from the primary available provider.

    Returns the first viable provider's client (Fireworks first, then
    Featherless), or ``None`` when no provider key is configured. This picks the
    *preferred* provider; per-request failover across providers is handled in
    :func:`_decision_from_llm`.
    """
    candidates = _resolve_inference_candidates()
    if not candidates:
        return None
    provider, api_key, base_url = candidates[0]
    return _new_llm(provider, api_key, base_url)


def _decision_from_llm(
    context_prompt: str,
    risk: dict,
    cfg: AgentConfig,
) -> dict | None:
    """Ask the LLM to decide under the options-only system prompt.

    Returns a parsed decision dict or ``None`` if the LLM is unavailable / the
    response cannot be parsed (caller falls back to the heuristic).
    """
    if not _resolve_inference_candidates() or cfg.heuristic_only:
        return None
    try:
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", OPTIONS_ONLY_SYSTEM_PROMPT),
                ("human", "{context_prompt}"),
            ]
        )
        # Runtime failover: try each configured provider in preference order until
        # one returns a parseable decision.
        candidate_count = 0
        for provider, api_key, base_url in _resolve_inference_candidates():
            candidate_count += 1
            llm = _new_llm(provider, api_key, base_url)
            chain = prompt | llm | JsonOutputParser()
            try:
                result = chain.invoke(
                    {
                        "context_prompt": context_prompt
                        + f"\n\nRISK SNAPSHOT:\n{json.dumps(risk, default=str)}"
                    }
                )
            except Exception as exc:  # noqa: BLE001 - try next provider
                logger.warning("LLM via %s failed (%s); trying next provider.", provider, exc)
                continue
            if not isinstance(result, dict) or "action" not in result:
                logger.warning("LLM via %s returned an unparseable decision.", provider)
                continue
            result.setdefault("confidence", 0.5)
            result.setdefault("justification", "")
            result.setdefault("hedge", None)
            return result
        if candidate_count == 0:
            logger.warning("No inference provider configured; using heuristic fallback.")
    except Exception as exc:  # pragma: no cover - LLM/network unreliable  # noqa: BLE001
        logger.warning("LLM decision failed (%s); using heuristic fallback.", exc)
        return None
    return None


def _context_prompt(perception: dict, recalled: Sequence[dict]) -> str:
    """Fold live perception + recalled memory into a concise prompt body."""
    blocks: list[str] = []
    acc = perception.get("account") or {}
    if acc:
        blocks.append(
            "ACCOUNT: equity=%s cash=%s buying_power=%s long_market_value=%s "
            "unrealized_pl=%s day_pnl=%s"
            % (
                acc.get("equity"),
                acc.get("cash"),
                acc.get("buying_power"),
                acc.get("long_market_value"),
                acc.get("unrealized_pl"),
                acc.get("day_pnl"),
            )
        )
    positions = perception.get("positions") or []
    if positions:
        pos_lines = []
        for p in positions:
            if isinstance(p, dict):
                pos_lines.append(
                    "{sym} qty={qty} mv={mv} pl={pl} class={cls}".format(
                        sym=p.get("symbol"),
                        qty=p.get("qty"),
                        mv=p.get("market_value"),
                        pl=p.get("unrealized_pl"),
                        cls=p.get("asset_class"),
                    )
                )
        blocks.append("POSITIONS:\n" + "\n".join(pos_lines))
    if recalled:
        mem_lines = [
            "- " + str(r.get("document", ""))[:200]
            for r in recalled
            if r.get("document")
        ]
        if mem_lines:
            blocks.append("RECALLED MEMORY (semantic context):\n" + "\n".join(mem_lines))
    return "\n\n".join(blocks)

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _persist_decision(
    run_id: uuid.UUID,
    model: str,
    risk: dict,
    decision: dict,
    degraded: bool,
    owner=None,
) -> None:
    from agent.models import Decision

    Decision.objects.create(
        run_id=run_id,
        model=model,
        action=str(decision.get("action", "hold"))[:32],
        confidence=_dec(decision.get("confidence") or 0).quantize(Decimal("0.0001")),
        summary=str(decision.get("justification", ""))[:5000],
        payload={"decision": decision},
        risk_snapshot={k: str(v) for k, v in risk.items()},
        degraded=degraded,
        owner_id=(owner.id if owner is not None else None),
    )


def _persist_order(
    run_id: uuid.UUID,
    symbol: str,
    payload: dict,
    *,
    strategy: str,
    is_hedge: bool,
    owner=None,
) -> None:
    from agent.models import OrderRecord

    OrderRecord.objects.create(
        order_id=str(payload.get("id", "")),
        client_order_id=str(payload.get("client_order_id", "")),
        symbol=str(symbol or payload.get("symbol") or "?"),
        side="buy",
        order_type=str(payload.get("order_type", payload.get("type", "market"))),
        qty=_dec(payload.get("qty", payload.get("contracts") or 0)),
        status=str(payload.get("status", "new")),
        strategy=strategy,
        is_hedge=is_hedge,
        source=OrderRecord.SOURCE_BRAIN,
        payload=payload,
        owner_id=(owner.id if owner is not None else None),
    )


def _persist_hedge(
    *,
    underlying: str,
    occ_symbol: str,
    contracts: int,
    strike: Any,
    expiry: str,
    order_id: str,
    protection_floor: Any,
    notes: str = "autonomous protective put",
    owner=None,
) -> None:
    from agent.models import Hedge

    Hedge.objects.create(
        underlying=underlying,
        occ_symbol=occ_symbol,
        contracts=int(contracts),
        strike=_dec(strike),
        expiry=date.fromisoformat(str(expiry)),
        protection_floor=_dec(protection_floor),
        status=Hedge.STATUS_PENDING,
        order_id=order_id,
        notes=notes,
        owner_id=(owner.id if owner is not None else None),
    )


def _persist_snapshot(risk: dict, *, num_positions: int = 0, owner=None) -> None:
    from agent.models import PortfolioSnapshot

    PortfolioSnapshot.objects.create(
        equity=_dec(risk.get("equity") or 0),
        cash=_dec(risk.get("cash") or 0),
        long_market_value=_dec(risk.get("long_market_value") or 0),
        unrealized_pl=_dec(risk.get("unrealized_pl") or 0),
        day_pnl=_dec(risk.get("day_pnl") or 0),
        num_positions=num_positions,
        num_option_positions=0,
        at_risk_exposure=_dec(risk.get("at_risk_exposure") or 0),
        hedge_coverage_ratio=_dec(risk.get("hedge_coverage_ratio") or 0),
        metadata={"source": "brain_cycle"},
        owner_id=(owner.id if owner is not None else None),
    )

# ---------------------------------------------------------------------------
# Execution (options orders via the MCP client)
# ---------------------------------------------------------------------------


def _execute_hedge(
    decision: dict,
    positions: Sequence[dict],
    cfg: AgentConfig,
    client: Any,
    run_id: uuid.UUID,
    owner=None,
) -> list[dict]:
    """Buy a protective put for the first uncovered long position (if any)."""
    executions: list[dict] = []
    if cfg.dry_run:
        return executions

    hedge = (decision.get("hedge") or {}) or {}
    underlying = hedge.get("underlying", "")
    if not underlying:
        pos = next(
            (
                p
                for p in positions
                if isinstance(p, dict)
                and str(p.get("asset_class") or "").lower() not in ("us_option", "option")
                and str(p.get("side") or "long").lower() != "short"
            ),
            None,
        )
        underlying = (pos or {}).get("symbol", "") if pos else ""
    if not underlying:
        return executions

    pos = next(
        (p for p in positions if isinstance(p, dict) and p.get("symbol") == underlying),
        None,
    )
    shares = _dec((pos or {}).get("qty") or 0)
    spot = _dec(
        (pos or {}).get("current_price")
        or (pos or {}).get("asset_current_price")
        or 0
    )
    if shares <= 0 or spot <= 0:
        logger.warning("Cannot hedge %s: missing shares/spot.", underlying)
        return executions

    floor_ratio = _dec(hedge.get("floor_ratio") or cfg.protection_floor_ratio, "0.90")
    try:
        params = compute_protective_put(shares, spot, floor_ratio, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hedge design failed for %s: %s", underlying, exc)
        return executions

    contracts = int(params["contracts"])
    if contracts <= 0:
        logger.info("Position %s too small for one 100-share contract.", underlying)
        return executions

    try:
        available = client.get_option_contracts(
            underlying, expiration_date=params["expiry"]
        )
        pick = select_protective_put_contract(
            available, params["target_strike"], params["expiry"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Option chain lookup for %s failed: %s", underlying, exc)
        pick = None

    if pick is None:
        executions.append(
            {
                "skipped": True,
                "symbol": underlying,
                "reason": "no suitable put contract resolved",
                "design": {
                    "target_strike": str(params["target_strike"]),
                    "floor": str(params["floor"]),
                    "expiry": params["expiry"],
                },
            }
        )
        return executions

    occ = pick["occ_symbol"] if isinstance(pick, dict) else str(pick)
    strike = (
        pick.get("strike", params["target_strike"])
        if isinstance(pick, dict)
        else params["target_strike"]
    )
    try:
        result = client.place_option_order(
            occ_symbol=occ,
            side="buy",
            qty=contracts,
            position_intent="buy_to_open",
            order_type="market",
        )
        result = result if isinstance(result, dict) else {"id": str(result), "status": "unknown"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Protective put order failed for %s", underlying)
        return [{"error": str(exc), "symbol": underlying}]

    _persist_order(run_id, occ, result, strategy="protective_put", is_hedge=True, owner=owner)
    try:
        _persist_hedge(
            underlying=underlying,
            occ_symbol=occ,
            contracts=contracts,
            strike=strike,
            expiry=params["expiry"],
            order_id=str(result.get("id", "")),
            protection_floor=params["floor"],
            owner=owner,
        )
    except Exception as exc:  # pragma: no cover  # noqa: BLE001
        logger.warning("Hedge audit write failed: %s", exc)

    executions.append(
        {
            "symbol": underlying,
            "occ_symbol": occ,
            "contracts": contracts,
            "strike": str(strike),
            "expiry": params["expiry"],
            "order_id": result.get("id"),
            "status": result.get("status"),
            "source": "mcp",
        }
    )
    return executions


def _execute_emergency(
    client: Any, cfg: AgentConfig, run_id: uuid.UUID
) -> list[dict]:
    """On a catastrophic drawdown, cancel rests and flatten the hedged book."""
    if cfg.dry_run:
        return [{"simulated": True, "action": "emergency_flat"}]
    results: list[dict] = []
    try:
        cancel = client.cancel_all_orders()
        results.append(
            {"cancel_all": cancel if isinstance(cancel, dict) else {"status": "ok"}}
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"cancel_all_error": str(exc)})
    try:
        flat = client.close_all_positions()
        results.append(
            {"flat": flat if isinstance(flat, dict) else {"status": "ok"}}
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"flat_error": str(exc)})
    return results


def _lookup_spot_for(symbol: str, client: Any) -> float:
    """Return the latest trade price for ``symbol`` via the MCP client."""
    if client is None:
        raise LookupError("no client for spot lookup")
    try:
        data = client.get_stock_latest_trade(symbol)
    except Exception:  # noqa: BLE001
        raise LookupError("spot lookup failed")

    def _from_node(node: Any) -> float | None:
        if not isinstance(node, dict):
            return None
        for key in ("price", "p", "trade_price", "last"):
            val = node.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return None

    if isinstance(data, dict):
        # Prefer a direct {trade: {...}} / {latest_trade: {...}} wrapper.
        for key in ("trade", "latest_trade", "result", "data"):
            val = _from_node(data.get(key))
            if val is not None:
                return val
        # FastMCP/Alpaca snapshot shape: {"trades": {"AAPL": {"p": ...}}}.
        raw_trades = data.get("trades")
        if isinstance(raw_trades, dict):
            val = _from_node(raw_trades.get(symbol))
            if val is not None:
                return val
            # Fall back to the first symbol in the payload.
            for key in raw_trades:
                val = _from_node(raw_trades.get(key))
                if val is not None:
                    return val
        # Bare symbol -> node map.
        val = _from_node(data.get(symbol))
        if val is not None:
            return val
        # Scan top-level values for a price-carrying node.
        for key in data:
            if key == "trades":
                continue
            val = _from_node(data[key] if isinstance(data[key], dict) else None)
            if val is not None:
                return val
    raise LookupError(f"no price for {symbol}")


def _execution_entry_for(
    decision: dict,
    positions: Sequence[dict],
    cfg: AgentConfig,
    risk: dict | None = None,
) -> dict:
    """Resolve the entry trade params (symbol, side, qty/notional) from a
    ``decision`` that carries either a ``trade`` block (LLM) or is synthesized
    heuristically."""

    trade = (decision.get("trade") or {}) or {}
    if trade:
        return trade

    # Heuristic synthesis: open a diversified large-cap with our budget.
    equity = _dec((risk or {}).get("equity") or 0)
    budget = equity * cfg.max_position_pct
    symbol = "SPY"
    qty = None
    try:
        spot = _dec(_lookup_spot_for(symbol, None), "0")
        if spot > 0:
            qty = int(budget // spot)
    except Exception:  # noqa: BLE001
        qty = None
    return {
        "strategy": "buy_stock",
        "symbol": symbol,
        "side": "buy",
        "qty": qty if qty and qty > 0 else None,
        "notional": str(budget) if not qty else None,
        "order_type": "market",
        "limit_price": None,
    }


def _execute_buy_stock(
    decision: dict,
    positions: Sequence[dict],
    cfg: AgentConfig,
    client: Any,
    run_id: uuid.UUID,
    owner=None,
) -> list[dict]:
    """Open a cash long stock position (or scale the first qualifying one)."""
    executions: list[dict] = []
    if cfg.dry_run or not cfg.auto_open_positions:
        return [
            {
                "simulated": True,
                "action": "buy_stock",
                "reason": "dry_run or auto_open_positions disabled",
            }
        ]

    trade = _execution_entry_for(decision, positions, cfg)
    symbol = trade.get("symbol", "")
    side = str(trade.get("side", "buy")).lower()
    if not symbol:
        executions.append({"skipped": True, "reason": "no symbol for buy_stock"})
        return executions

    # Position budget guards.
    equity = _dec(trade.get("_equity") or 0)
    if equity <= 0:
        executions.append({"skipped": True, "symbol": symbol, "reason": "no equity"})
        return executions
    budget = equity * cfg.max_position_pct

    qty_raw = trade.get("qty")
    notional_raw = trade.get("notional")
    qty = None
    notional = None
    if qty_raw:
        qty = int(_dec(qty_raw))
    elif notional_raw:
        notional = str(_dec(notional_raw))
    else:
        # Size from spot.
        try:
            spot = _lookup_spot_for(symbol, client)
        except Exception as exc:  # noqa: BLE001
            executions.append(
                {
                    "skipped": True,
                    "symbol": symbol,
                    "reason": f"spot lookup failed: {exc}",
                }
            )
            return executions
        qty = int(budget // _dec(spot)) if spot else 0
        if qty <= 0:
            executions.append(
                {
                    "skipped": True,
                    "symbol": symbol,
                    "reason": "budget too small for 1 share",
                }
            )
            return executions

    order_type = trade.get("order_type") or "market"
    limit_price = trade.get("limit_price")
    try:
        result = client.place_stock_order(
            symbol=symbol,
            side=side,
            qty=qty,
            notional=notional,
            order_type=order_type,
            limit_price=str(limit_price) if limit_price else None,
        )
        result = result if isinstance(result, dict) else {"id": str(result), "status": "unknown"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("buy_stock order failed for %s", symbol)
        return [{"error": str(exc), "symbol": symbol}]

    _persist_order(
        run_id,
        symbol,
        result,
        strategy="buy_stock",
        is_hedge=False,
        owner=owner,
    )
    executions.append(
        {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "notional": notional,
            "order_type": order_type,
            "order_id": result.get("id"),
            "status": result.get("status"),
            "source": "mcp",
        }
    )
    return executions


def _execute_covered_call(
    decision: dict,
    positions: Sequence[dict],
    cfg: AgentConfig,
    client: Any,
    run_id: uuid.UUID,
    owner=None,
) -> list[dict]:
    """Sell an out-of-the-money covered call against the first entitled long."""
    executions: list[dict] = []
    if cfg.dry_run:
        return executions

    trade = (decision.get("trade") or {}) or {}
    symbol = trade.get("symbol", "")
    if not symbol:
        pos = next(
            (
                p
                for p in positions
                if isinstance(p, dict)
                and str(p.get("asset_class") or "").lower() not in ("us_option", "option")
                and str(p.get("side") or "long").lower() != "short"
            ),
            None,
        )
        symbol = (pos or {}).get("symbol", "") if pos else ""
    if not symbol:
        return executions

    pos = next(
        (p for p in positions if isinstance(p, dict) and p.get("symbol") == symbol),
        None,
    ) or {}
    shares = _dec(pos.get("qty") or 0)
    spot = _dec(pos.get("current_price") or pos.get("asset_current_price") or 0)
    if shares <= 0 or spot <= 0:
        logger.warning("Cannot sell covered call for %s: missing shares/spot.", symbol)
        return executions

    contracts = int(shares // 100)
    if contracts <= 0:
        logger.info("Position %s too small for one 100-share contract.", symbol)
        return executions

    expiry = trade.get("expiry") or (
        date.today() + timedelta(days=cfg.hedge_days_to_expiry)
    ).isoformat()
    target_strike = trade.get("strike") or (spot * Decimal("1.10"))
    try:
        available = client.get_option_contracts(symbol, expiration_date=expiry)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Option chain lookup for %s failed: %s", symbol, exc)
        available = []

    occ = None
    best_strike = None
    for c in available or []:
        node = c if isinstance(c, dict) else {}
        if node.get("type", "").upper() != "CALL":
            continue
        strike = node.get("strike_price") or node.get("strike")
        if strike is None:
            continue
        try:
            f_strike = float(_dec(strike))
        except Exception:  # noqa: BLE001
            continue
        if f_strike >= float(_dec(target_strike)) and (
            best_strike is None or f_strike < best_strike
        ):
            best_strike = f_strike
            occ = node.get("occ_symbol") or node.get("symbol") or ""
    if not occ:
        executions.append(
            {
                "skipped": True,
                "symbol": symbol,
                "reason": "no eligible OTM call contract resolved",
            }
        )
        return executions

    try:
        result = client.place_option_order(
            occ_symbol=occ,
            side="sell",
            qty=contracts,
            position_intent="sell_to_open",
            order_type="market",
        )
        result = result if isinstance(result, dict) else {"id": str(result), "status": "unknown"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Covered call order failed for %s", symbol)
        return [{"error": str(exc), "symbol": symbol}]

    _persist_order(
        run_id, occ, result, strategy="covered_call", is_hedge=True, owner=owner
    )
    executions.append(
        {
            "symbol": symbol,
            "occ_symbol": occ,
            "contracts": contracts,
            "strike": str(best_strike) if best_strike else "",
            "expiry": expiry,
            "order_id": result.get("id"),
            "status": result.get("status"),
            "source": "mcp",
        }
    )
    return executions


def _execute_cash_secured_put(
    decision: dict,
    positions: Sequence[dict],
    cfg: AgentConfig,
    client: Any,
    run_id: uuid.UUID,
    owner=None,
) -> list[dict]:
    """Sell a cash-secured put to collect premium and (maybe) acquire the stock."""
    executions: list[dict] = []
    if cfg.dry_run or not cfg.auto_open_positions:
        return [
            {
                "simulated": True,
                "action": "cash_secured_put",
                "reason": "dry_run or auto_open_positions disabled",
            }
        ]

    trade = (decision.get("trade") or {}) or {}
    symbol = trade.get("symbol", "")
    if not symbol:
        return executions

    equity = _dec(trade.get("_equity") or 0)
    strike = trade.get("strike")
    expiry = trade.get("expiry")
    order_type = trade.get("order_type") or "market"
    limit_price = trade.get("limit_price")

    if equity <= 0:
        executions.append({"skipped": True, "symbol": symbol, "reason": "no equity"})
        return executions

    contracts = int(trade.get("qty") or 1)
    if not strike:
        try:
            spot = _lookup_spot_for(symbol, client)
            strike = spot
        except Exception as exc:  # noqa: BLE001
            executions.append(
                {
                    "skipped": True,
                    "symbol": symbol,
                    "reason": f"spot lookup failed: {exc}",
                }
            )
            return executions

    if not expiry:
        expiry = (date.today() + timedelta(days=cfg.hedge_days_to_expiry)).isoformat()

    try:
        available = client.get_option_contracts(symbol, expiration_date=expiry)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Option chain lookup for %s failed: %s", symbol, exc)
        available = []

    occ = None
    best_strike = None
    target = _dec(strike)
    for c in available or []:
        node = c if isinstance(c, dict) else {}
        if node.get("type", "").upper() != "PUT":
            continue
        s = node.get("strike_price") or node.get("strike")
        if s is None:
            continue
        try:
            f_s = float(_dec(s))
        except Exception:  # noqa: BLE001
            continue
        if best_strike is None or abs(f_s - float(target)) < abs(
            best_strike - float(target)
        ):
            best_strike = f_s
            occ = node.get("occ_symbol") or node.get("symbol") or ""
    if not occ:
        executions.append(
            {
                "skipped": True,
                "symbol": symbol,
                "reason": "no eligible put contract resolved",
            }
        )
        return executions

    try:
        result = client.place_option_order(
            occ_symbol=occ,
            side="sell",
            qty=contracts,
            position_intent="sell_to_open",
            order_type=order_type,
            limit_price=str(limit_price) if limit_price else None,
        )
        result = result if isinstance(result, dict) else {"id": str(result), "status": "unknown"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Cash-secured put order failed for %s", symbol)
        return [{"error": str(exc), "symbol": symbol}]

    _persist_order(
        run_id, occ, result, strategy="cash_secured_put", is_hedge=False, owner=owner
    )
    executions.append(
        {
            "symbol": symbol,
            "occ_symbol": occ,
            "contracts": contracts,
            "strike": str(best_strike) if best_strike else "",
            "expiry": expiry,
            "order_id": result.get("id"),
            "status": result.get("status"),
            "source": "mcp",
        }
    )
    return executions

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _gather_via_mcp(cfg: AgentConfig, credentials=None) -> tuple[dict, list[dict], dict, Any]:
    """Pull account/positions/history/clock through the MCP client.

    Returns ``(account, positions, history, client)``. On failure returns empty
    primitives and ``None`` client so the cycle degrades cleanly. ``credentials``
    (per-user Alpaca keys) is forwarded so the MCP subprocess is isolated to one
    account - never mixing simultaneous users.
    """
    from agent.mcp_client import AlpacaSyncClient

    try:
        client = AlpacaSyncClient(
            cfg.mcp_command or None, cfg.toolsets, credentials=credentials
        )
        account = client.get_account_info() or {}
        positions = client.get_positions() or []
        history: dict = {}
        try:
            got = client.get_portfolio_history(period="1M", timeframe="1D")
            history = got if isinstance(got, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("Portfolio history unavailable: %s", exc)
        return account, positions, history, client
    except Exception as exc:  # pragma: no cover - MCP/creds unavailable  # noqa: BLE001
        logger.warning("MCP gather failed (%s); degrading to DB-only context.", exc)
        return {}, [], {}, None


def run_agent_cycle_sync(
    dry_run: bool | None = None,
    heuristic_only: bool | None = None,
    *,
    credentials=None,
    owner=None,
) -> dict:
    """Run one full agent cognitive cycle synchronously and return its summary.

    Accepts optional overrides for ``dry_run`` / ``heuristic_only`` (from the API
    or CLI), plus per-user ``credentials`` (Alpaca keys, for MCP isolation) and
    ``owner`` (``UserProfile`` the resulting audit rows are attributed to).
    Produces a decision, persists audit tables, and - when not dry-run - submits
    option orders via the MCP client.
    """
    overrides: dict = {}
    # Layered precedence: explicit request params > per-profile config > env
    # defaults. Profile overrides come first so a non-None request value can
    # still win for a one-off run (e.g. an explicitly dry-run single cycle).
    if owner is not None:
        profile_values = AgentConfig.profile_overrides(owner)
        if profile_values:
            overrides.update(profile_values)
    if dry_run is not None:
        overrides["dry_run"] = dry_run
    if heuristic_only is not None:
        overrides["heuristic_only"] = heuristic_only
    cfg = AgentConfig.from_settings(overrides)

    run_id = uuid.uuid4()
    errors: list[str] = []

    account, positions, history, client = _gather_via_mcp(cfg, credentials=credentials)

    # Fallback context when MCP is down: use the latest persisted snapshot.
    if not account:
        try:
            from agent.models import PortfolioSnapshot, Position

            snap = PortfolioSnapshot.objects.filter(
                owner_id=(owner.id if owner is not None else None)
            ).order_by("-created_at").first()
            if snap:
                account = {
                    "equity": snap.equity,
                    "cash": snap.cash,
                    "long_market_value": snap.long_market_value,
                    "unrealized_pl": snap.unrealized_pl,
                    "day_pnl": snap.day_pnl,
                }
            positions = [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "market_value": p.market_value,
                    "unrealized_pl": p.unrealized_pl,
                    "asset_class": p.asset_class,
                }
                for p in Position.objects.filter(
                    owner_id=(owner.id if owner is not None else None)
                )
            ]
            errors.append("live MCP unavailable; used persisted context")
        except Exception as exc:  # pragma: no cover  # noqa: BLE001
            errors.append(f"context unavailable: {exc}")

    # Perception -> memory (store the facts we just gathered).
    stored = 0
    try:
        from agent.data_perception import ingest_text

        acc = account or {}
        stored += len(
            ingest_text(
                (
                    f"Account: equity={acc.get('equity')} cash={acc.get('cash')} "
                    f"long_market_value={acc.get('long_market_value')} "
                    f"unrealized_pl={acc.get('unrealized_pl')} day_pnl={acc.get('day_pnl')}"
                ),
                document_title="account state",
                source="account",
                owner=owner,
            )
        )
        for pos in positions or []:
            if not isinstance(pos, dict):
                continue
            stored += len(
                ingest_text(
                    (
                        f"Position {pos.get('symbol')}: qty={pos.get('qty')} "
                        f"market_value={pos.get('market_value')} "
                        f"asset_class={pos.get('asset_class')}"
                    ),
                    document_title="position",
                    ticker_scope=str(pos.get("symbol", "")),
                    source="positions",
                    owner=owner,
                )
            )
    except Exception as exc:  # pragma: no cover - storage ancillary  # noqa: BLE001
        logger.debug("Memory ingest skipped: %s", exc)

    # Recall relevant memory (semantic context for the decision).
    recalled: list[dict] = []
    try:
        from agent.data_perception import embed_text

        probe = (account or {}).get("equity")
        recalled = semantic_recall(
            f"recent positions risk protection floor equity {probe}",
            k=5,
            owner=owner,
        )
        _ = embed_text
    except Exception as exc:  # pragma: no cover - recall ancillary  # noqa: BLE001
        logger.debug("Semantic recall skipped: %s", exc)

    # Risk + decide.
    risk = assess_risk(account, positions, cfg)
    decision = _heuristic_decision(risk, cfg)
    model = "heuristic"
    degraded = True
    if not cfg.heuristic_only:
        llm_decision = _decision_from_llm(
            _context_prompt(
                {"account": account or {}, "positions": positions or []},
                recalled,
            ),
            risk,
            cfg,
        )
        if llm_decision:
            decision = llm_decision
            model = cfg.llm_model or "llm"
            degraded = False

    action = str(decision.get("action", "hold"))
    conf = _dec(decision.get("confidence") or 0)

    # Persist the decision + snapshot.
    try:
        _persist_decision(run_id, model, risk, decision, degraded, owner=owner)
        _persist_snapshot(risk, num_positions=len(positions or []), owner=owner)
    except Exception as exc:  # pragma: no cover - table write  # noqa: BLE001
        errors.append(f"persistence failed: {exc}")

    # Execute (only when not dry-run).
    executions: list[dict] = []
    # The entry executors read equity for position sizing; inject it.
    if decision.get("trade") is not None and isinstance(decision.get("trade"), dict):
        decision["trade"].setdefault("_equity", risk.get("equity"))
    if action in ("hedge", "protective_put"):
        executions.extend(_execute_hedge(decision, positions or [], cfg, client, run_id, owner=owner))
    elif action == "buy_stock":
        executions.extend(_execute_buy_stock(decision, positions or [], cfg, client, run_id, owner=owner))
    elif action == "cash_secured_put":
        executions.extend(_execute_cash_secured_put(decision, positions or [], cfg, client, run_id, owner=owner))
    elif action == "covered_call":
        executions.extend(_execute_covered_call(decision, positions or [], cfg, client, run_id, owner=owner))
    elif action == "emergency":
        executions.extend(_execute_emergency(client, cfg, run_id))
    elif action == "close_hedge":
        if not cfg.dry_run and client is not None:
            try:
                from agent.models import Hedge

                closed = Hedge.objects.filter(
                    status=Hedge.STATUS_OPEN,
                    owner_id=(owner.id if owner is not None else None),
                )[:20]
                for h in closed:
                    cc = client.close_position(h.occ_symbol)
                    executions.append(
                        {
                            "symbol": h.occ_symbol,
                            "close": cc if isinstance(cc, dict) else {"status": "ok"},
                        }
                    )
            except Exception as exc:  # pragma: no cover  # noqa: BLE001
                errors.append(f"close_hedge failed: {exc}")

    return {
        "run_id": str(run_id),
        "source": "llm" if not degraded else "heuristic",
        "degraded": degraded,
        "action": action,
        "confidence": None if conf is None else float(conf),
        "justification": str(decision.get("justification", "")),
        "model": model,
        "risk": {k: str(v) for k, v in risk.items()},
        "memory_stored_chunks": stored,
        "memory_recalled": len(recalled),
        "executions": executions,
        "errors": errors,
    }


async def run_agent_cycle(
    dry_run: bool | None = None,
    heuristic_only: bool | None = None,
    *,
    credentials=None,
    owner=None,
) -> dict:
    """Async facade around ``run_agent_cycle_sync`` for async callers/views."""
    return await asyncio.to_thread(
        run_agent_cycle_sync,
        dry_run=dry_run,
        heuristic_only=heuristic_only,
        credentials=credentials,
        owner=owner,
    )
