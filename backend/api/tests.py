# Django fill API test for per-profile agent runtime configuration.

import json

from django.test import Client, TestCase

from user.models import UserProfile


class AgentConfigFlowTests(TestCase):
    """Per-profile agent runtime config: GET, PATCH, and reset to env."""

    def setUp(self) -> None:
        self.client = Client()
        # Bind the silent session to a real profile.
        self.profile = UserProfile.objects.create(
            slug="cfg-user",
            display_name="Config User",
        )
        self.profile.store_credentials(api_key="KEY", secret_key="SEC")
        self.profile.save()
        self.client.post(
            "/api/auth/bootstrap",
            data='{"slug":"cfg-user"}',
            content_type="application/json",
        )

    def _get(self) -> dict:
        res = self.client.get("/api/agent/config")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def _patch(self, payload: dict) -> dict:
        res = self.client.patch(
            "/api/agent/config",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_start_inherits_env_defaults(self) -> None:
        cfg = self._get()
        self.assertFalse(cfg["profile_override"])
        # Server/env defaults surfaced (dry_run defaults True, auto_open off).
        self.assertTrue(isinstance(cfg["dry_run"], bool))
        self.assertFalse(cfg["auto_open_positions"])
        self.assertEqual(cfg["max_positions"], 5)

    def test_patch_partial_persists_for_profile(self) -> None:
        cfg = self._patch({"auto_open_positions": True, "max_positions": 8})
        self.assertTrue(cfg["auto_open_positions"])
        self.assertEqual(cfg["max_positions"], 8)
        self.assertEqual(cfg["max_position_pct"], 0.1)  # untouched field kept
        self.assertTrue(cfg["profile_override"])

        # Confirmed persisted + still effective on a fresh read.
        cfg = self._get()
        self.assertTrue(cfg["auto_open_positions"])
        self.assertEqual(cfg["max_positions"], 8)

        # The profile row really holds the override.
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.has_agent_config)
        self.assertEqual(self.profile.agent_max_positions, 8)

    def test_reset_to_env_clears_overrides(self) -> None:
        self._patch({"auto_open_positions": True, "max_positions": 8})
        cfg = self._patch({"reset_to_env": True})
        self.assertFalse(cfg["auto_open_positions"])
        self.assertEqual(cfg["max_positions"], 5)
        self.assertFalse(cfg["profile_override"])

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.has_agent_config)

    def test_unauthenticated_rejected(self) -> None:
        plain = Client()
        self.assertEqual(plain.get("/api/agent/config").status_code, 401)
