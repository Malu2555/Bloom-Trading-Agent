"""
Tests for the cookie-based silent auth + per-user credential isolation.

Covers:
* unauthenticated requests are rejected (401) by ``require_auth``;
* ``/api/auth/bootstrap`` silently binds the HttpOnly session to a profile;
* ``/api/auth/whoami`` reflects the session state;
* per-user ``AlpacaCredentials`` payloads and their ``to_environ()`` overrides
  carry *only* that user's keys (the anti cross-contamination guarantee);
* dashboard reads are scoped to the bound profile's owner;
* the perception layer resolves per-user credentials (no shared singletons);
* semantic memory is owned + recalled per user.
"""

from django.test import Client, TestCase

from agent.models import Position
from user.credentials import AlpacaCredentials, resolve_credentials
from user.models import UserProfile


class SilentAuthFlowTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_unauthenticated_read_rejected(self) -> None:
        res = self.client.get("/api/portfolio")
        self.assertEqual(res.status_code, 401)

    def test_bootstrap_binds_session_and_whoami(self) -> None:
        res = self.client.post(
            "/api/auth/bootstrap", data="{}", content_type="application/json"
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["slug"], "default")
        self.assertTrue(data["has_credentials"])

        whoami = self.client.get("/api/auth/whoami")
        self.assertEqual(whoami.status_code, 200)
        self.assertTrue(whoami.json()["authenticated"])

        # Authenticated now - returns 404 (authed) rather than 401 (anonymous).
        portfolio = self.client.get("/api/portfolio")
        self.assertEqual(portfolio.status_code, 404)

    def test_resolve_credentials_from_session(self) -> None:
        self.client.post(
            "/api/auth/bootstrap", data="{}", content_type="application/json"
        )
        # Extract the bound credentials by replaying the session the test client
        # ended up with (Django test Client surfaces it as .session).
        session = self.client.session
        session["user_profile_id"] = UserProfile.objects.get(slug="default").id
        session.save()
        # Drive through the request pipeline via whoami? No - call the resolver
        # with a lightweight request built from the session.
        from django.contrib.sessions.backends.db import SessionStore

        s = SessionStore()
        s["user_profile_id"] = UserProfile.objects.get(slug="default").id
        s.create()
        from django.http import HttpRequest

        req = HttpRequest()
        req.session = s
        creds = resolve_credentials(req)
        assert creds is not None, "bound session must resolve to credentials"
        self.assertIsInstance(creds, AlpacaCredentials)
        self.assertEqual(creds.paper, True)
        self.assertIn("ALPACA_API_KEY", creds.to_environ())

    def test_credentials_per_user_isolated(self) -> None:
        alice = UserProfile.objects.create(slug="alice", display_name="Alice")
        alice.store_credentials(api_key="KEY-ALICE", secret_key="SEC-ALICE")
        alice.save()
        bob = UserProfile.objects.create(slug="bob", display_name="Bob")
        bob.store_credentials(api_key="KEY-BOB", secret_key="SEC-BOB")
        bob.save()

        a_env = AlpacaCredentials.from_profile(alice).to_environ()
        b_env = AlpacaCredentials.from_profile(bob).to_environ()

        self.assertEqual(a_env["ALPACA_API_KEY"], "KEY-ALICE")
        self.assertEqual(b_env["ALPACA_API_KEY"], "KEY-BOB")
        self.assertNotIn("KEY-BOB", a_env.values())
        self.assertNotIn("KEY-ALICE", b_env.values())
        # Paper-only guardrail is always on.
        self.assertEqual(a_env["ALPACA_PAPER_MODE"], "true")

    def test_owner_scoped_reads_per_user(self) -> None:
        alice = UserProfile.objects.create(slug="alice")
        alice.store_credentials(api_key="KEY-A", secret_key="SEC-A")
        alice.save()
        bob = UserProfile.objects.create(slug="bob")
        bob.store_credentials(api_key="KEY-B", secret_key="SEC-B")
        bob.save()
        Position.objects.create(symbol="AAPL", owner=alice)
        Position.objects.create(symbol="MSFT", owner=bob)

        c_alice = Client()
        c_alice.post(
            "/api/auth/bootstrap",
            data='{"slug":"alice"}',
            content_type="application/json",
        )
        symbols = [p["symbol"] for p in c_alice.get("/api/positions").json()]
        self.assertEqual(symbols, ["AAPL"])

        c_bob = Client()
        c_bob.post(
            "/api/auth/bootstrap",
            data='{"slug":"bob"}',
            content_type="application/json",
        )
        symbols = [p["symbol"] for p in c_bob.get("/api/positions").json()]
        self.assertEqual(symbols, ["MSFT"])

    def test_perception_clients_isolated_per_user(self) -> None:
        """SDK clients are built per-credential identity (no shared singletons)."""
        from unittest import mock

        from agent import data_perception as dp
        from user.credentials import AlpacaCredentials

        alice = UserProfile.objects.create(slug="alice")
        alice.store_credentials(api_key="KEY-ALICE", secret_key="SEC-ALICE")
        alice.save()
        bob = UserProfile.objects.create(slug="bob")
        bob.store_credentials(api_key="KEY-BOB", secret_key="SEC-BOB")
        bob.save()
        creds_a = AlpacaCredentials.from_profile(alice)
        creds_b = AlpacaCredentials.from_profile(bob)

        captured = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                captured.append((args, kwargs))

        dp._client_cache.clear()
        with (
            mock.patch.object(dp, "_ALPACA_SDK", True),
            mock.patch.object(dp, "StockHistoricalDataClient", FakeClient),
            mock.patch.object(dp, "TradingClient", FakeClient),
        ):
            a_stock = dp.stock_client(creds_a)
            b_stock = dp.stock_client(creds_b)
            dp.trading_client(creds_a)

        stocks = [c for c in captured if not c[1]]  # StockHistoricalDataClient(key, secret)
        trading = [c for c in captured if c[1]]  # TradingClient(key, secret, paper=...)
        self.assertEqual(stocks[0][0], ("KEY-ALICE", "SEC-ALICE"))
        self.assertEqual(stocks[1][0], ("KEY-BOB", "SEC-BOB"))
        self.assertEqual(trading[0][0][:2], ("KEY-ALICE", "SEC-ALICE"))
        self.assertEqual(trading[0][1], {"paper": True})
        self.assertIsNot(a_stock, b_stock)  # distinct per user
        self.assertEqual(
            set(dp._client_cache),
            {f"user:{creds_a.profile_id}", f"user:{creds_b.profile_id}"},
        )

    def test_memory_owner_scoped(self) -> None:
        """Semantic memory stores/recalls facts attributed to the owning profile."""
        from unittest import mock

        from agent import brain

        alice = UserProfile.objects.create(slug="alice", display_name="Alice")
        fake_collection = mock.Mock()

        with (
            mock.patch.object(brain, "_pgvector_available", return_value=False),
            mock.patch.object(
                brain, "_default_embedder", lambda *a, **k: lambda t: [0.0] * 3
            ),
            mock.patch.object(brain, "_get_chroma_collection", return_value=fake_collection),
        ):
            brain.store_semantic_knowledge("AAPL rallied", owner=alice)

        self.assertTrue(fake_collection.add.called)
        kwargs = fake_collection.add.call_args
        self.assertEqual(kwargs.kwargs["metadatas"][0]["owner_id"], alice.id)

    def test_ingest_route_uses_request_credentials(self) -> None:
        """/api/agent/ingest is guarded and forwards the session's credentials."""
        from unittest import mock

        from agent import data_perception as dp

        alice = UserProfile.objects.create(slug="alice")
        alice.store_credentials(api_key="KEY-ALICE", secret_key="SEC-ALICE")
        alice.save()

        c = Client()
        c.post(
            "/api/auth/bootstrap",
            data='{"slug":"alice"}',
            content_type="application/json",
        )
        captured = {}

        def _fake_ingest(symbols, credentials, owner):
            captured["creds"] = credentials
            captured["owner_id"] = owner.id if owner else None
            return ["doc-1", "doc-2"]

        with mock.patch.object(dp, "ingest_perception", side_effect=_fake_ingest):
            res = c.post(
                "/api/agent/ingest",
                data='{"symbols":["AAPL"]}',
                content_type="application/json",
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["stored"], 2)
        self.assertIsNotNone(captured.get("creds"))
        self.assertEqual(captured["creds"].profile_id, alice.id)
        self.assertEqual(captured["owner_id"], alice.id)

