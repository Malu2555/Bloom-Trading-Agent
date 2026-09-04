"""
Pydantic validation input/output structures for the Django-ninja REST API.

These schemas shape the data moving between the browser/agent and the
persistence layer (``agent.models``). Model-backed schemas are kept tight so the
raw broker payloads are never exposed directly.
"""

from decimal import Decimal

from ninja import ModelSchema, Schema

from agent.models import (
    Decision,
    Hedge,
    JobRun,
    OptionPosition,
    OrderRecord,
    PortfolioSnapshot,
    Position,
)


class PortfolioOut(ModelSchema):
    """Latest portfolio snapshot."""

    class Meta:
        model = PortfolioSnapshot
        fields = [
            "id",
            "equity",
            "cash",
            "buying_power",
            "long_market_value",
            "unrealized_pl",
            "day_pnl",
            "at_risk_exposure",
            "hedge_coverage_ratio",
            "num_positions",
            "num_option_positions",
            "created_at",
        ]


class PositionOut(ModelSchema):
    class Meta:
        model = Position
        fields = [
            "symbol",
            "asset_class",
            "side",
            "qty",
            "avg_entry_price",
            "current_price",
            "market_value",
            "unrealized_pl",
            "unrealized_plpc",
            "updated_at",
        ]


class OptionPositionOut(ModelSchema):
    class Meta:
        model = OptionPosition
        fields = [
            "occ_symbol",
            "underlying",
            "option_type",
            "strike",
            "expiry",
            "qty",
            "avg_entry_price",
            "mark_price",
            "market_value",
            "unrealized_pl",
            "implied_vol",
            "delta",
            "is_hedge",
            "status",
            "updated_at",
        ]


class TradeOut(ModelSchema):
    class Meta:
        model = OrderRecord
        fields = [
            "id",
            "order_id",
            "client_order_id",
            "symbol",
            "side",
            "order_type",
            "qty",
            "filled_avg_price",
            "status",
            "strategy",
            "is_hedge",
            "source",
            "created_at",
        ]


class DecisionOut(ModelSchema):
    class Meta:
        model = Decision
        fields = [
            "run_id",
            "model",
            "action",
            "confidence",
            "summary",
            "degraded",
            "created_at",
        ]


class HedgeOut(ModelSchema):
    class Meta:
        model = Hedge
        fields = [
            "id",
            "underlying",
            "occ_symbol",
            "contracts",
            "strike",
            "expiry",
            "premium_total",
            "protection_floor",
            "status",
            "order_id",
            "current_pnl",
            "opened_at",
            "created_at",
        ]


class JobOut(ModelSchema):
    class Meta:
        model = JobRun
        fields = [
            "job_id",
            "name",
            "status",
            "message",
            "result",
            "started_at",
            "finished_at",
            "created_at",
        ]


# --- Input structures ------------------------------------------------------


class AgentRunIn(Schema):
    """Request to run the agent decision loop."""

    dry_run: bool | None = None
    heuristic_only: bool | None = None
    run_in_background: bool = True


class IngestIn(Schema):
    """Request to pull + persist current perception as owned semantic memory."""

    symbols: list[str] | None = None


# schemas.py uses ``from __future__ import annotations``, so ``list[str]`` is a
# string forward-ref; force resolution now to avoid ninja/pydantic lazy-typing
# "not fully defined" errors when IngestIn is used as a request body.
IngestIn.model_rebuild()


class SummaryOut(Schema):
    """Human-facing result of an agent cycle."""

    run_id: str
    source: str
    degraded: bool
    action: str
    confidence: float | None = None
    justification: str = ""
    executions: list[dict] = []
    errors: list[str] = []


class AgentConfigOut(Schema):
    """Effective agent runtime config for the authenticated profile.

    Mixes env/settings defaults with any concrete per-profile overrides. The
    ``profile_override`` flag tells the UI whether the profile is pinning the
    values (vs. just inheriting the server defaults).
    """

    dry_run: bool
    auto_open_positions: bool
    heuristic_only: bool
    max_position_pct: float
    max_positions: int
    min_cash_reserve: float
    profile_override: bool = False


class AgentConfigIn(Schema):
    """Partial update for per-profile agent runtime config.

    Any field left ``None`` is left untouched. A ``reset_to_env`` flag clears
    *all* per-profile overrides so the user reverts to server/env defaults.
    """

    dry_run: bool | None = None
    auto_open_positions: bool | None = None
    heuristic_only: bool | None = None
    max_position_pct: Decimal | None = None
    max_positions: int | None = None
    min_cash_reserve: Decimal | None = None
    reset_to_env: bool = False


class EmergencyFlatIn(Schema):
    """Close the whole book (cancel orders + flatten)."""

    confirm: bool = True


class EmergencyCloseIn(Schema):
    symbol: str


class EmergencyHedgeIn(Schema):
    """Hardcoded protective put purchase for a held position."""

    symbol: str
    shares: Decimal | None = None
    spot: Decimal | None = None
    floor_ratio: Decimal = Decimal("0.90")
