"""Pydantic input/output schemas for the silent-auth endpoints."""

from ninja import Schema


class BootstrapIn(Schema):
    """Optional body for silent bootstrap: bind to an existing profile ``slug``."""

    slug: str | None = None


class ProfileOut(Schema):
    """Public, secrets-free view of a bound profile."""

    slug: str
    display_name: str = ""
    paper: bool = True
    has_credentials: bool = False


class WhoAmIOut(Schema):
    """Silent-auth status for the current session."""

    authenticated: bool
    profile: ProfileOut | None = None
