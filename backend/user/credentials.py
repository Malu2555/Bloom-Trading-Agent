"""
Credentials plumbing: turn a silent (HttpOnly) session cookie into one user's
Alpaca sandbox keys, packaged as an in-memory payload for the worker loops.

Highlights
----------
* ``resolve_credentials(request)`` -> ``AlpacaCredentials | None``
    Reads ``request.session`` (backed by the HttpOnly ``sessionid`` cookie,
    parsed by Django's ``SessionMiddleware``), looks up the bound
    ``UserProfile`` in the Neon database and decrypts its keys into an immutable
    in-memory payload.

* ``AlpacaCredentials.to_environ()``
    The subprocess-level isolation primitive. Each user's MCP client / emergency
    executor spawns a *fresh* subprocess seeded from **this** user's env, so two
    simultaneous dashboards never observe each other's keys.

None of these helpers read global ``os.environ`` for the *secret* values -- the
whole point is that every request carries its own credentials. The only
exception is ``provision_default_profile()`` used by the silent bootstrap to
seed a first profile from the repo's ``.env`` defaults.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

from django.http import HttpRequest

# Session key that stores the id of the UserProfile a session is bound to.
SESSION_PROFILE_KEY = "user_profile_id"


class CredentialsError(RuntimeError):
    """Raised when a user's Alpaca credentials cannot be resolved."""


@dataclass(frozen=True)
class AlpacaCredentials:
    """Immutable in-memory payload handed to mcp_client / brain worker loops."""

    profile_id: int
    slug: str
    api_key: str
    secret_key: str
    base_url: str = "https://paper-api.alpaca.markets"
    data_url: str = "https://data.alpaca.markets"
    paper: bool = True

    # Env var names the alpaca-mcp-server reads; keep in sync with
    # ``agent.mcp_client._ENV_BRIDGE``.
    _ENV_MAP: dict[str, str] = field(
        default_factory=lambda: {
            "api_key": "ALPACA_API_KEY",
            "secret_key": "ALPACA_API_SECRET_KEY",
            "base_url": "ALPACA_BASE_URL",
            "data_url": "ALPACA_DATA_URL",
            "paper": "ALPACA_PAPER_MODE",
        },
        repr=False,
    )

    def to_dict(self) -> dict[str, Any]:
        """Plain dict payload. For debugging; never log its secrets."""
        return asdict(self)

    def to_environ(self) -> dict[str, str]:
        """Subprocess env overrides that isolate this user's broker session."""
        env = {
            self._ENV_MAP["api_key"]: self.api_key,
            self._ENV_MAP["secret_key"]: self.secret_key,
        }
        if self.base_url:
            env[self._ENV_MAP["base_url"]] = self.base_url
        if self.data_url:
            env[self._ENV_MAP["data_url"]] = self.data_url
        env[self._ENV_MAP["paper"]] = "true" if self.paper else "false"
        return env

    @classmethod
    def from_profile(cls, profile) -> AlpacaCredentials:
        """Build the payload from a resolved ``user.models.UserProfile``."""
        if not profile.has_credentials:
            raise CredentialsError(
                f"Profile '{profile.slug}' has no Alpaca credentials configured."
            )
        return cls(
            profile_id=profile.id,
            slug=profile.slug,
            api_key=profile.decrypted_api_key,
            secret_key=profile.decrypted_secret_key,
            base_url=(profile.base_url or "https://paper-api.alpaca.markets"),
            data_url=(profile.data_url or "https://data.alpaca.markets"),
            paper=True,  # sandbox-only: never allow live trading from a dashboard.
        )


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def bind_profile(request: HttpRequest, profile) -> None:
    """Bind the session (and its HttpOnly cookie) to ``profile``."""
    request.session[SESSION_PROFILE_KEY] = profile.id
    request.session.modified = True
    request.session.set_expiry(60 * 60 * 12)  # silent half-day session


def unbind_profile(request: HttpRequest) -> None:
    request.session.pop(SESSION_PROFILE_KEY, None)
    request.session.modified = True


def resolve_profile(request: HttpRequest):
    """Return the ``UserProfile`` the session cookie is bound to, or ``None``."""
    from .models import UserProfile  # local import avoids app-load order issues

    profile_id = request.session.get(SESSION_PROFILE_KEY)
    if not profile_id:
        return None
    try:
        return UserProfile.objects.get(id=profile_id, is_active=True)
    except UserProfile.DoesNotExist:
        request.session.pop(SESSION_PROFILE_KEY, None)
        request.session.modified = True
        return None


def resolve_credentials(request: HttpRequest) -> AlpacaCredentials | None:
    """Resolve one user's Alpaca sandbox credentials from the session cookie.

    Returns ``None`` for an anonymous session (no bound profile). Raises
    :class:`CredentialsError` when the bound profile has no usable keys.
    """
    profile = resolve_profile(request)
    if profile is None:
        return None
    return AlpacaCredentials.from_profile(profile)


# ---------------------------------------------------------------------------
# Provisioning (silent bootstrap)
# ---------------------------------------------------------------------------


def provision_default_profile():
    """Create-or-update the env-seeded default profile.

    Enables a zero-config first boot: the first dashboard visitor is silently
    authenticated against the repo's default Alpaca sandbox keys. Admins can
    add more profiles in the Django admin and hand out the relevant ``slug`` to
    real users, who then bind to it via ``POST /api/auth/bootstrap``.
    """
    from .models import UserProfile

    default_api = os.environ.get("ALPACA_API_KEY", "").strip()
    default_secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    base_url = (
        os.environ.get("ALPACA_BASE_URL", "").strip()
        or "https://paper-api.alpaca.markets"
    )
    data_url = (
        os.environ.get("ALPACA_DATA_URL", "").strip()
        or "https://data.alpaca.markets"
    )

    profile, _ = UserProfile.objects.get_or_create(
        slug="default",
        defaults={
            "display_name": "Default sandbox user",
            "base_url": base_url,
            "data_url": data_url,
            "paper": True,
            "auto_approvisioned": True,
        },
    )
    # Seed keys from env only when the auto-provisioned profile still has no
    # real keys of its own. Never overwrite admin-configured keys.
    if default_api and default_secret and (
        not profile.decrypted_api_key or profile.auto_approvisioned
    ):
        profile.store_credentials(api_key=default_api, secret_key=default_secret)
        profile.save(update_fields=["api_key", "secret_key", "updated_at"])
    return profile

