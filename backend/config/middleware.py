"""Custom middleware.

``DisableCsrfForApi`` marks ``/api/*`` requests as CSRF-exempt so cookie-based
API consumers (the Vue dashboard sending ``X-CSRFToken``, cURL, and the Swagger
UI at ``/api/docs``) can make mutating calls without a CSRF header. Django's
``CsrfViewMiddleware`` (which runs before this in reverse) checks the flag that
this middleware sets, so admin + non-API forms remain protected.
"""

from django.utils.deprecation import MiddlewareMixin

API_PREFIXES = ("/api/", "/api")


class DisableCsrfForApi(MiddlewareMixin):
    """Skip CSRF enforcement for the JSON API namespace."""

    def process_view(self, request, view_func, view_args, view_kwargs):  # noqa: N802
        if request.path.startswith(API_PREFIXES):
            # CsrfViewMiddleware.process_view returns early when this is True,
            # so /api mutating calls never hit the CSRF 403.
            request._dont_enforce_csrf_checks = True  # type: ignore[attr-defined]
        return None
