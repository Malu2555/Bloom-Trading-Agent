"""
Persistence layer for the autonomous AI trading agent.

Tracks portfolio state, stock/crypto positions, option contracts (used for
hedging), every order submitted, AI decisions, active hedges and background
job status. This data drives the P&L reporting and the human-facing API.

All monetary fields are stored as Decimal to avoid floating point drift.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone

# --- pgvector support ---------------------------------------------------------
# Real embedding column: text is turned into a vector here. It is native
# ``vector(n)`` on PostgreSQL + pgvector (production/Neon); it degrades to a
# plain BLOB on SQLite so local ``migrate``/``check`` still pass (local semantic
# search is delegated to Chroma in ``agent.brain``).
VECTOR_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))

try:
    from pgvector.django import VectorField

    _PGVECTOR_IMPORTABLE = True
except Exception:  # pragma: no cover - dependency not installed  # noqa: BLE001
    from django.db.models import JSONField

    VectorField = None  # type: ignore[assignment, misc]
    _PGVECTOR_IMPORTABLE = False


def _pgvector_enabled() -> bool:
    """True when running against PostgreSQL with the pgvector extension."""
    if not _PGVECTOR_IMPORTABLE:
        return False
    try:
        from django.conf import settings

        engine = str(settings.DATABASES.get("default", {}).get("ENGINE", ""))
    except Exception:  # pragma: no cover  # noqa: BLE001
        return False
    return "postgresql" in engine.lower()


if VectorField is not None:

    class VectorStoreField(VectorField):
        def db_type(self, connection) -> str:
            if connection.vendor == "postgresql":
                return super().db_type(connection)
            return "BLOB"

else:
    VectorStoreField = JSONField  # type: ignore[misc]


class TimeStampedModel(models.Model):
    """Abstract base providing created/updated timestamps."""

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PortfolioSnapshot(TimeStampedModel):
    """Point-in-time capture of the account used for P&L reporting and memory."""

    equity = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    cash = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    buying_power = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    long_market_value = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    unrealized_pl = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    day_pnl = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))             
    num_positions = models.IntegerField(default=0)# you cannot set default=None because it cannot be used in math calculations, so we set it to 0 instead.
    num_option_positions = models.IntegerField(default=0)
    at_risk_exposure = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    hedge_coverage_ratio = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0.0000"))
    metadata = models.JSONField(default=dict, blank=True)
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} equity={self.equity}"


class Position(TimeStampedModel):
    """Open stock/ETF/crypto position reported by the broker."""

    ASSET_STOCK = "stock"
    ASSET_CRYPTO = "crypto"
    ASSET_US_OPTION = "us_option"
    ASSET_CHOICES = (
        (ASSET_STOCK, "Stock / ETF"),
        (ASSET_CRYPTO, "Crypto"),
        (ASSET_US_OPTION, "US Option"),
    )

    SIDE_LONG = "long"
    SIDE_SHORT = "short"
    SIDE_CHOICES = ((SIDE_LONG, "Long"), (SIDE_SHORT, "Short"))

    symbol = models.CharField(max_length=32, db_index=True)
    asset_class = models.CharField(
        max_length=16, choices=ASSET_CHOICES, default=ASSET_STOCK
    )
    side = models.CharField(max_length=8, choices=SIDE_CHOICES, default=SIDE_LONG)
    qty = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal("0.00000000"))
    avg_entry_price = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0.000000"))
    current_price = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0.000000"))
    market_value = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    unrealized_pl = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    unrealized_plpc = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0.000000"))
    # Owner scoping: prevents one dashboard user from seeing another's book.
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("symbol",)
        unique_together = (("owner", "symbol"),)

    def __str__(self) -> str:
        return f"{self.symbol} {self.qty}@{self.avg_entry_price}"


class OptionPosition(TimeStampedModel):
    """Open option contract. Options are the hedging instrument for the agent."""

    TYPE_CALL = "C"
    TYPE_PUT = "P"
    TYPE_CHOICES = ((TYPE_CALL, "Call"), (TYPE_PUT, "Put"))

    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
        (STATUS_EXPIRED, "Expired"),
    )

    occ_symbol = models.CharField(
        max_length=64, help_text="OCC option symbol, e.g. AAPL250321C00150000"
    )
    underlying = models.CharField(max_length=16, db_index=True)
    option_type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    strike = models.DecimalField(max_digits=20, decimal_places=4)
    expiry = models.DateField(db_index=True)
    qty = models.IntegerField(default=0, help_text="Number of contracts")
    side = models.CharField(max_length=16, default="buy")
    avg_entry_price = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0.000000"))
    mark_price = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal("0.000000"))
    market_value = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    unrealized_pl = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    implied_vol = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    delta = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    is_hedge = models.BooleanField(default=False, help_text="True if opened by the risk engine")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True
    )
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("underlying", "expiry", "strike")
        unique_together = (("owner", "occ_symbol"),)

    def __str__(self) -> str:
        return f"{self.occ_symbol} {self.qty}@{self.avg_entry_price}"


class OrderRecord(TimeStampedModel):
    """Every order submitted by the agent, the CLI emergency path, or an operator."""

    SOURCE_BRAIN = "brain"
    SOURCE_EMERGENCY_CLI = "emergency_cli"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = (
        (SOURCE_BRAIN, "Brain"),
        (SOURCE_EMERGENCY_CLI, "Emergency CLI"),
        (SOURCE_MANUAL, "Manual"),
    )

    SIDE_BUY = "buy"
    SIDE_SELL = "sell"
    SIDE_CHOICES = ((SIDE_BUY, "Buy"), (SIDE_SELL, "Sell"))

    STRATEGY_PP = "protective_put"
    STRATEGY_HEDGE = "hedge"
    STRATEGY_UNWIND = "unwind"
    STRATEGY_EMERGENCY = "emergency_flat"
    STRATEGY_MANUAL = "manual"
    STRATEGY_CASH_SECURED_PUT = "cash_secured_put"
    STRATEGY_COVERED_CALL = "covered_call"
    STRATEGY_SPREAD = "spread"
    STRATEGY_BUY_STOCK = "buy_stock"
    STRATEGY_CHOICES = (
        (STRATEGY_PP, "Protective Put"),
        (STRATEGY_HEDGE, "Hedge"),
        (STRATEGY_UNWIND, "Unwind"),
        (STRATEGY_EMERGENCY, "Emergency flat"),
        (STRATEGY_MANUAL, "Manual"),
        (STRATEGY_CASH_SECURED_PUT, "Cash-Secured Put"),
        (STRATEGY_COVERED_CALL, "Covered Call"),
        (STRATEGY_SPREAD, "Spread"),
        (STRATEGY_BUY_STOCK, "Buy Stock"),
    )

    order_id = models.CharField(max_length=64, blank=True, db_index=True)
    client_order_id = models.CharField(max_length=64, blank=True, db_index=True)
    symbol = models.CharField(max_length=64, db_index=True)
    side = models.CharField(max_length=8, choices=SIDE_CHOICES)
    order_type = models.CharField(max_length=32, default="market")
    qty = models.DecimalField(max_digits=20, decimal_places=8, default=Decimal("0.00000000"))
    limit_price = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    filled_avg_price = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    notional = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=32, default="new", db_index=True)
    strategy = models.CharField(max_length=32, choices=STRATEGY_CHOICES, default=STRATEGY_MANUAL)
    is_hedge = models.BooleanField(default=False)
    source = models.CharField(
        max_length=32, choices=SOURCE_CHOICES, default=SOURCE_BRAIN
    )
    payload = models.JSONField(default=dict, blank=True)
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.strategy} {self.side} {self.qty} {self.symbol} [{self.status}]"


class Hedge(TimeStampedModel):
    """Tracks a protective put hedge from opening through to expiry/close."""

    STATUS_PENDING = "pending"
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
        (STATUS_EXPIRED, "Expired"),
    )

    underlying = models.CharField(max_length=16, db_index=True)
    occ_symbol = models.CharField(max_length=64)
    contracts = models.IntegerField(default=0)
    strike = models.DecimalField(max_digits=20, decimal_places=4)
    expiry = models.DateField()
    premium_total = models.DecimalField(
        max_digits=20, decimal_places=2, default=Decimal("0.00"), help_text="Total cost of the puts (debit)"
    )
    protection_floor = models.DecimalField(max_digits=20, decimal_places=4, default=Decimal("0.0000"))
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    order_id = models.CharField(max_length=64, blank=True)
    current_pnl = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.underlying} {self.contracts}x {self.occ_symbol} [{self.status}]"


class Decision(TimeStampedModel):
    """An AI (or heuristic) decision produced by a brain run, for audit + memory."""

    ACTION_HOLD = "hold"
    ACTION_HEDGE = "hedge"
    ACTION_UNWIND = "unwind"
    ACTION_EMERGENCY = "emergency"
    ACTION_CLOSE_HEDGE = "close_hedge"
    ACTION_BUY_STOCK = "buy_stock"
    ACTION_CASH_SECURED_PUT = "cash_secured_put"
    ACTION_COVERED_CALL = "covered_call"
    ACTION_SPREAD = "spread"
    ACTION_CHOICES = (
        (ACTION_HOLD, "Hold"),
        (ACTION_HEDGE, "Hedge (protective put)"),
        (ACTION_BUY_STOCK, "Buy stock"),
        (ACTION_CASH_SECURED_PUT, "Cash-secured put"),
        (ACTION_COVERED_CALL, "Covered call"),
        (ACTION_SPREAD, "Spread"),
        (ACTION_UNWIND, "Unwind / reduce risk"),
        (ACTION_EMERGENCY, "Emergency flat"),
        (ACTION_CLOSE_HEDGE, "Close hedge"),
    )

    run_id = models.UUIDField(db_index=True)
    model = models.CharField(max_length=128, blank=True, default="")
    action = models.CharField(max_length=32, choices=ACTION_CHOICES, default=ACTION_HOLD)
    confidence = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True
    )
    summary = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    risk_snapshot = models.JSONField(default=dict, blank=True)
    degraded = models.BooleanField(default=False, help_text="True if heuristic, not LLM")
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.run_id} {self.action} ({self.model})"


class JobRun(TimeStampedModel):
    """Status for non-blocking background jobs surfaced by the API (job status paths)."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    job_id = models.UUIDField(unique=True, db_index=True)
    name = models.CharField(max_length=128)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    message = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"


class EmbeddedRiskContext(TimeStampedModel):
    """One vectorized, ingested perception chunk stored by the agent.

    The agent's "perception" is market data pulled directly from Alpaca
    (account, positions, historical stock bars, option chains/snapshots) via
    ``agent.data_perception``. Each ingested - chunked + embedded - piece of
    data becomes one row here. ``embedding_vector`` is a **pgvector-backed**
    vector column (real text -> vector embeddings), searched with ``<=>`` cosine
    distance in production (Neon). On SQLite it degrades to a BLOB; local
    semantic search there is delegated to the Chroma store in ``agent.brain``.
    """

    SENTIMENT_CHOICES = (
        ("positive", "Positive"),
        ("negative", "Negative"),
        ("neutral", "Neutral"),
    )

    SOURCE_CHOICES = (
        ("account", "Account"),
        ("positions", "Positions"),
        ("stock_bars", "Stock historical bars"),
        ("option_chain", "Option chain"),
        ("option_snapshot", "Option snapshot"),
        ("decision", "Agent decision"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_title = models.CharField(max_length=255, blank=True, default="")
    raw_text_chunk = models.TextField(
        help_text="One chunk of ingested perception text (agent-facing)."
    )
    embedding_vector = VectorStoreField(
        dimensions=VECTOR_DIMENSIONS,
        help_text="pgvector embedding of ``raw_text_chunk`` (text -> vector).",
    )
    ticker_scope = models.CharField(
        max_length=16, blank=True, default="", db_index=True,
        help_text="Ticker this chunk pertains to (blank = whole account).",
    )
    sentiment_tag = models.CharField(
        max_length=16, blank=True, default="",
        choices=SENTIMENT_CHOICES,
        help_text="Optional sentiment label assigned to the chunk.",
    )
    source = models.CharField(
        max_length=32, default="positions", choices=SOURCE_CHOICES, db_index=True,
        help_text="Which kind of perception data produced this chunk.",
    )
    published_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="Market-data timestamp of the source snapshot.",
    )
    ingested_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    # Owner scoping: memory is attributed to (and recalled per) one UserProfile so
    # concurrent dashboards never bleed semantic context into one another.
    owner = models.ForeignKey(
        "user.UserProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=True,
    )

    class Meta:
        ordering = ("-ingested_at",)

    def __str__(self) -> str:
        return f"{self.source}:{self.ticker_scope}:{self.raw_text_chunk[:60]}"

