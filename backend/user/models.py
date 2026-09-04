"""
Models for the lightweight cookie-based silent-auth layer.

A single ``UserProfile`` row packs everything a user needs to operate the
dashboard: display identity plus their *personal* Alpaca sandbox credentials.
The API key and secret are **encrypted at rest** with Django's ``SECRET_KEY``
so the database never holds plaintext secrets.

Auth is intentionally light. The browser holds an HttpOnly session cookie; the
server reads it, resolves the ``UserProfile`` the session is bound to, and feeds
that one user's credentials into the worker loop (``mcp_client`` / ``brain``).
Because every worker builds an isolated MCP subprocess env from *its own*
profile, two users can hit the dashboard at the same time without mixing
credentials or broker data.
"""

from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.db import models
from django.utils import timezone

# Salt used to encrypt Alpaca keys at rest. Binding to SECRET_KEY means the
# ciphertext is useless if the DB leaks but the key does not.
_SALT = "userprofile.alpaca.credentials"


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return signing.dumps({"v": value}, key=settings.SECRET_KEY, salt=_SALT)


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return str(
            signing.loads(value, key=settings.SECRET_KEY, salt=_SALT).get("v", "")
        )
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        ValueError,
        TypeError,
    ):
        return ""


class UserProfile(models.Model):
    """One dashboard user bound to their own Alpaca sandbox account."""

    # Explicit PK so static type checkers (Pylance/mypy) see the auto-created id
    # field instead of relying on Django's metaclass magic. Matches Django's
    # 3.2+ default (BigAutoField) => identical runtime behavior, no migration.
    id = models.BigAutoField(primary_key=True)

    slug = models.SlugField(max_length=64, unique=True, db_index=True)
    display_name = models.CharField(max_length=128, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    auto_approvisioned = models.BooleanField(
        default=False,
        help_text="True for the env-seeded default profile created at bootstrap.",
    )

    # --- Alpaca sandbox credentials (encrypted at rest) ------------------
    # The admin form accepts plaintext and encrypts here; raw DB values are always
    # signed+encrypted so they never leak in dumps, logs or migrations.
    api_key = models.TextField(blank=True, default="")
    secret_key = models.TextField(blank=True, default="")
    base_url = models.CharField(
        max_length=256, blank=True, default="https://paper-api.alpaca.markets"
    )
    data_url = models.CharField(
        max_length=256, blank=True, default="https://data.alpaca.markets"
    )
    paper = models.BooleanField(default=True)

    # --- Agent runtime configuration (per-profile overrides) ------------------
    # ``None``/NULL means "not configured here -> fall back to env/settings
    # defaults" (settings.AGENT_CONFIG). Once a profile row holds a concrete
    # value, that value wins over env vars so the UI can control the agent.
    agent_dry_run = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Simulate decisions without submitting real orders.",
    )
    agent_auto_open = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Let the agent buy stock / sell cash-secured puts on its own.",
    )
    agent_heuristic_only = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Skip the LLM and use the deterministic heuristic fallback.",
    )
    agent_max_position_pct = models.DecimalField(
        null=True,
        blank=True,
        default=None,
        max_digits=6,
        decimal_places=4,
        help_text="Max %% of equity per stock position (e.g. 0.10 = 10%%).",
    )
    agent_max_positions = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=None,
        help_text="Max concurrent stock positions the agent may hold.",
    )
    agent_min_cash_reserve = models.DecimalField(
        null=True,
        blank=True,
        default=None,
        max_digits=14,
        decimal_places=2,
        help_text="Cash floor the agent must never trade below.",
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)

    def __str__(self) -> str:
        return self.display_name or self.slug

    # -- secrets: plaintext never lives in model fields or repr -----------------
    @property
    def decrypted_api_key(self) -> str:
        return _decrypt(self.api_key)

    @property
    def decrypted_secret_key(self) -> str:
        return _decrypt(self.secret_key)

    @property
    def has_credentials(self) -> bool:
        return bool(self.decrypted_api_key and self.decrypted_secret_key)

    def store_credentials(self, *, api_key: str, secret_key: str) -> None:
        """Encrypt and persist new Alpaca keys on this profile."""
        self.api_key = _encrypt(api_key)
        self.secret_key = _encrypt(secret_key)

    # -- agent config helpers --------------------------------------------------
    AGENT_CONFIG_FIELDS = (
        "agent_dry_run",
        "agent_auto_open",
        "agent_heuristic_only",
        "agent_max_position_pct",
        "agent_max_positions",
        "agent_min_cash_reserve",
    )

    @property
    def has_agent_config(self) -> bool:
        """True if the profile overrides at least one agent setting."""
        return any(getattr(self, f, None) is not None for f in self.AGENT_CONFIG_FIELDS)

    def clear_agent_config(self) -> None:
        """Forget all per-profile agent overrides (fall back to env defaults)."""
        for f in self.AGENT_CONFIG_FIELDS:
            setattr(self, f, None)
        self.save(update_fields=list(self.AGENT_CONFIG_FIELDS) + ["updated_at"])
