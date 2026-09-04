"""
Request guards for the Ninja API.

``require_auth`` is a thin replacement for heavy login machinery: it reads the
silent session cookie, resolves the user's Alpaca credentials and attaches them
to ``request.auth``. If the session has no bound profile (or that profile has no
usable keys) it rejects the request with 401 *before* any broker call happens.

``optional_auth`` does the same but never rejects -- view code can branch on
``request.auth`` being None.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from ninja.errors import HttpError

from .credentials import CredentialsError, resolve_credentials

P = ParamSpec("P")
R = TypeVar("R")


def _resolve(request):
    """Resolve credentials, translating missing keys into a 409-style error."""
    try:
        return resolve_credentials(request)
    except CredentialsError as exc:
        raise HttpError(409, str(exc)) from exc


def require_auth(func: Callable[P, R]) -> Callable[P, R]:
    """Guard a Ninja view with the silent session credential check.

    On success attaches ``request.auth`` -- an
    :class:`~user.credentials.AlpacaCredentials` payload -- for the handler to
    feed into the worker loops (``mcp_client``/``brain``).
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        request = args[0]
        creds = _resolve(request)
        if creds is None:
            raise HttpError(
                401,
                "No active session. Send POST /api/auth/bootstrap first.",
            )
        request.auth = creds  # type: ignore[attr-defined]
        return func(*args, **kwargs)

    return wrapper


def optional_auth(func: Callable[P, R]) -> Callable[P, R]:
    """Attach credentials to ``request.auth`` when available; never rejects."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        request = args[0]
        try:
            request.auth = resolve_credentials(request)  # type: ignore[attr-defined]
        except CredentialsError:
            request.auth = None  # type: ignore[attr-defined]
        return func(*args, **kwargs)

    return wrapper
