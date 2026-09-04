"""
REST API (Django-ninja) for the trading agent.

Read endpoints surface the tracking tables; ``/agent/cycle`` runs one decision
cycle; ``/emergency/*`` expose the hardcoded CLI fallback; ``/refresh`` syncs
live Alpaca state. Ninja auto-publishes Swagger at /api/docs and the schema at
/api/openapi.json. Behavior is logged via ``logger``.
"""

import logging
from decimal import Decimal

from ninja import NinjaAPI
from ninja.errors import HttpError

from agent import brain
from agent.models import (
    Decision,
    Hedge,
    JobRun,
    OptionPosition,
    OrderRecord,
    PortfolioSnapshot,
    Position,
)
from user.credentials import (
    bind_profile,
    provision_default_profile,
    unbind_profile,
)
from user.decorators import optional_auth, require_auth
from user.models import UserProfile
from user.schemas import BootstrapIn, ProfileOut, WhoAmIOut

from .schemas import (
    AgentConfigIn,
    AgentConfigOut,
    AgentRunIn,
    DecisionOut,
    EmergencyCloseIn,
    EmergencyFlatIn,
    EmergencyHedgeIn,
    HedgeOut,
    IngestIn,
    JobOut,
    OptionPositionOut,
    PortfolioOut,
    PositionOut,
    SummaryOut,
    TradeOut,
)

logger = logging.getLogger(__name__)

api = NinjaAPI(title="Alpaca Autonomous Trading Agent", version="1.0.0")


def _executor_for(request):
    """Build a fresh EmergencyExecutor scoped to the request's profile.

    Always returns a *new* instance keyed to this request's credentials so two
    simultaneous users never share (or mix) Alpaca sandbox sessions.
    """
    from tools.cli_runner import EmergencyExecutor

    return EmergencyExecutor(
        credentials=request.auth,
        owner=_profile_for(request),
    )


def _profile_for(request):
    """Resolve the bound ``UserProfile`` for a guarded request (or ``None``)."""
    if getattr(request, "auth", None) is None:
        return None
    try:
        return UserProfile.objects.get(id=request.auth.profile_id, is_active=True)
    except UserProfile.DoesNotExist:
        return None


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@api.get("/portfolio", response=PortfolioOut)
@require_auth
def get_portfolio(request):
    snap = (
        PortfolioSnapshot.objects.filter(owner_id=request.auth.profile_id)
        .order_by("-created_at")
        .first()
    )
    if snap is None:
        raise HttpError(404, "No portfolio snapshot yet. Run /agent/cycle first.")
    return snap


@api.get("/positions", response=list[PositionOut])
@require_auth
def get_positions(request):
    return list(Position.objects.filter(owner_id=request.auth.profile_id))


@api.get("/options", response=list[OptionPositionOut])
@require_auth
def get_option_positions(request):
    return list(
        OptionPosition.objects.filter(
            owner_id=request.auth.profile_id, status="open"
        )
    )


@api.get("/trades", response=list[TradeOut])
@require_auth
def get_trades(request, limit: int = 100):
    return list(
        OrderRecord.objects.filter(owner_id=request.auth.profile_id)
        .order_by("-created_at")[: int(limit)]
    )


@api.get("/decisions", response=list[DecisionOut])
@require_auth
def get_decisions(request, limit: int = 50):
    return list(
        Decision.objects.filter(owner_id=request.auth.profile_id)
        .order_by("-created_at")[: int(limit)]
    )


@api.get("/hedges", response=list[HedgeOut])
@require_auth
def get_hedges(request, limit: int = 100):
    return list(
        Hedge.objects.filter(owner_id=request.auth.profile_id)
        .order_by("-created_at")[: int(limit)]
    )


@api.get("/jobs", response=list[JobOut])
@require_auth
def get_jobs(request, limit: int = 50):
    return list(
        JobRun.objects.filter(owner_id=request.auth.profile_id)
        .order_by("-created_at")[: int(limit)]
    )


@api.get("/jobs/{job_id}", response=JobOut)
@require_auth
def get_job(request, job_id: str):
    job = JobRun.objects.filter(
        job_id=job_id, owner_id=request.auth.profile_id
    ).first()
    if job is None:
        raise HttpError(404, "Unknown job id.")
    return job


# ---------------------------------------------------------------------------
# Broker sync (pull live state into the tracking tables)
# ---------------------------------------------------------------------------


@api.post("/refresh", response=dict)
@require_auth
def refresh_from_broker(request):
    """Pull account + positions from this user's Alpaca sandbox into local tables."""
    from agent.mcp_client import AlpacaSyncClient

    owner = _profile_for(request)
    client = AlpacaSyncClient(credentials=request.auth)
    account = client.get_account_info() or {}
    live_positions = client.get_positions() or []
    updated = 0
    for pos in live_positions:
        symbol = pos.get("symbol")
        if not symbol:
            continue
        Position.objects.update_or_create(
            symbol=symbol,
            owner_id=request.auth.profile_id,
            defaults={
                "asset_class": pos.get("asset_class", Position.ASSET_STOCK),
                "side": pos.get("side", Position.SIDE_LONG),
                "qty": Decimal(str(pos.get("qty", 0))),
                "avg_entry_price": Decimal(str(pos.get("avg_entry_price", 0))),
                "current_price": Decimal(str(pos.get("current_price", 0))),
                "market_value": Decimal(str(pos.get("market_value", 0))),
                "unrealized_pl": Decimal(str(pos.get("unrealized_pl", 0))),
                "unrealized_plpc": Decimal(str(pos.get("unrealized_plpc", 0))),
            },
        )
        updated += 1
    live_symbols = {p.get("symbol") for p in live_positions}
    Position.objects.filter(
        owner_id=request.auth.profile_id
    ).exclude(symbol__in=live_symbols).delete()

    PortfolioSnapshot.objects.create(
        equity=Decimal(
            str(account.get("equity") or account.get("portfolio_value") or 0)
        ),
        cash=Decimal(str(account.get("cash") or 0)),
        buying_power=Decimal(str(account.get("buying_power") or 0)),
        long_market_value=Decimal(str(account.get("long_market_value") or 0)),
        unrealized_pl=Decimal(str(account.get("unrealized_pl") or 0)),
        day_pnl=Decimal(str(account.get("day_pnl") or 0)),
        num_positions=len(live_positions),
        num_option_positions=0,
        at_risk_exposure=sum(
            (Decimal(str(p.get("market_value", 0))) for p in live_positions),
            Decimal(0),
        ),
        metadata={"source": "broker_sync"},
        owner_id=(owner.id if owner is not None else None),
    )
    return {
        "updated_positions": updated,
        "account_equity": str(account.get("equity")),
    }


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------


@api.post("/agent/cycle", response=SummaryOut)
@require_auth
def run_agent_sync(request, body: AgentRunIn):
    """Run one agent cycle synchronously and return the summary.

    Runs the langchain/brain loop wired to **this** user's Alpaca sandbox
    credentials (``request.auth``) so concurrent dashboards never trade or read
    another user's account.
    """
    kwargs: dict = {}
    if body.dry_run is not None:
        kwargs["dry_run"] = body.dry_run
    if body.heuristic_only is not None:
        kwargs["heuristic_only"] = body.heuristic_only
    kwargs["credentials"] = request.auth
    kwargs["owner"] = _profile_for(request)
    summary = brain.run_agent_cycle_sync(**kwargs)
    return {
        "run_id": summary.get("run_id", ""),
        "source": summary.get("source", "unknown"),
        "degraded": bool(summary.get("degraded")),
        "action": summary.get("action", "hold"),
        "confidence": summary.get("confidence"),
        "justification": summary.get("justification", ""),
        "executions": summary.get("executions", []),
        "errors": summary.get("errors", []),
    }


@api.post("/agent/ingest", response=dict)
@require_auth
def ingest_perception(request, body: IngestIn):
    """Pull this user's perception and persist it as their owned semantic memory.

    Runs through ``agent.data_perception`` with the request's per-user Alpaca
    credentials, so the fetched account/positions/bars and the resulting memory
    are isolated to the authenticated profile (no cross-user bleed).
    """
    from agent.data_perception import ingest_perception as _ingest

    ids = _ingest(body.symbols, credentials=request.auth, owner=_profile_for(request))
    return {"stored": len(ids)}


@api.get("/agent/config", response=AgentConfigOut)
@require_auth
def get_agent_config(request):
    """Return the authenticated profile's effective agent runtime config."""
    return brain.AgentConfig.effective_for(_profile_for(request))


@api.patch("/agent/config", response=AgentConfigOut)
@require_auth
def update_agent_config(request, body: AgentConfigIn):
    """Persist per-profile agent runtime overrides (partial update).

    Only provided fields are written. ``reset_to_env=True`` clears every
    per-profile override so the user reverts to the server/env defaults.
    """
    profile = _profile_for(request)
    if profile is None:
        raise HttpError(404, "No bound profile.")

    if body.reset_to_env:
        profile.clear_agent_config()
        return brain.AgentConfig.effective_for(profile)

    fields_to_update: list[str] = []
    for profile_field, config_key in brain.AgentConfig.PROFILE_FIELD_MAP.items():
        # profile_field is the UserProfile column (e.g. "agent_dry_run");
        # config_key is the AgentConfig/body name (e.g. "dry_run").
        val = getattr(body, config_key)
        if val is None:
            continue
        setattr(profile, profile_field, val)
        fields_to_update.append(profile_field)

    if fields_to_update:
        fields_to_update.append("updated_at")
        profile.save(update_fields=fields_to_update)
    return brain.AgentConfig.effective_for(profile)


# ---------------------------------------------------------------------------
# Emergency (hardcoded CLI fallback) endpoints
# ---------------------------------------------------------------------------


@api.post("/emergency/cancel-all", response=dict)
@require_auth
def emergency_cancel_all(request):
    executor = _executor_for(request)
    return executor.cancel_all()


@api.post("/emergency/flat", response=dict)
@require_auth
def emergency_flat(request, body: EmergencyFlatIn):
    if not body.confirm:
        return {"status": "aborted", "message": "confirmation required"}
    executor = _executor_for(request)
    return executor.close_all()


@api.post("/emergency/close", response=dict)
@require_auth
def emergency_close(request, body: EmergencyCloseIn):
    executor = _executor_for(request)
    return executor.close_position(body.symbol)


@api.post("/emergency/hedge", response=dict)
@require_auth
def emergency_hedge(request, body: EmergencyHedgeIn):
    executor = _executor_for(request)
    return executor.protect_position(
        symbol=body.symbol,
        shares=body.shares,
        spot=body.spot,
        floor_ratio=body.floor_ratio,
    )


# ---------------------------------------------------------------------------
# Silent authentication (cookie-based)
# ---------------------------------------------------------------------------


@api.post("/auth/bootstrap", response=ProfileOut)
def auth_bootstrap(request, body: BootstrapIn | None = None):
    """Silently provision + bind a profile to this HttpOnly session cookie.

    No passwords, no challenge: the browser already carries the ``sessionid``
    cookie; we just associate it with a ``UserProfile`` and return a secrets-free
    summary. An optional ``slug`` binds to an existing (admin-created) profile.
    """
    body = body or BootstrapIn()
    if body.slug:
        profile = UserProfile.objects.filter(
            slug=body.slug, is_active=True
        ).first()
        if profile is None:
            raise HttpError(404, f"Unknown profile slug '{body.slug}'.")
    else:
        # Zero-config first boot: use the env-seeded default profile.
        profile = provision_default_profile()

    bind_profile(request, profile)
    return {
        "slug": profile.slug,
        "display_name": profile.display_name,
        "paper": profile.paper,
        "has_credentials": profile.has_credentials,
    }


@api.get("/auth/whoami", response=WhoAmIOut)
@optional_auth
def auth_whoami(request):
    """Return the current session's auth status (secrets never exposed)."""
    if getattr(request, "auth", None) is None:
        return {"authenticated": False, "profile": None}
    profile = _profile_for(request)
    return {
        "authenticated": profile is not None,
        "profile": (
            {
                "slug": profile.slug,
                "display_name": profile.display_name,
                "paper": profile.paper,
                "has_credentials": profile.has_credentials,
            }
            if profile is not None
            else None
        ),
    }


@api.post("/auth/logout", response=dict)
@optional_auth
def auth_logout(request):
    """Forget the bound profile (keeps the HttpOnly session cookie alive)."""
    unbind_profile(request)
    return {"status": "ok"}
