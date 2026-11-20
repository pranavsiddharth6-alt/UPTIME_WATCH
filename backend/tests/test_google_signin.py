import json
import os
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt

from app import create_app
from app.services import google_signin
from app.services.limits import RateLimited


ORIGIN = "https://dashboard.example.net"
HEADERS = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
CLIENT_ID = "1234-unittest.apps.googleusercontent.com"
ISSUER = "https://accounts.google.com"
CHALLENGE_COOKIE = "__Host-uptimewatch_google"
SESSION_COOKIE = "__Host-uptimewatch_admin"


class GoogleSignInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.signer = crypt.RSASigner.from_string(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
        ))
        cls.public_key = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def setUp(self):
        with patch.dict(os.environ, {
            "UPTIMEWATCH_ENV": "production", "UPTIMEWATCH_FRONTEND_ORIGIN": ORIGIN,
            "GOOGLE_CLIENT_ID": CLIENT_ID, "ADMIN_API_TOKEN": "",
        }):
            self.app = create_app()
        self.http = self.app.test_client()

        mock_client = MagicMock()
        for module in ("google_signin", "sessions"):
            self.enterContext(patch(f"app.services.{module}.get_mongo_client", mock_client))
        database = mock_client.return_value.__enter__.return_value.__getitem__.return_value
        self.users, self.challenges, self.sessions, buckets = (MagicMock() for _ in range(4))
        database.__getitem__.side_effect = {
            "google_users": self.users, "google_signin_challenges": self.challenges,
            "admin_sessions": self.sessions, "monitoring_rate_limits": buckets,
        }.__getitem__
        buckets.find_one_and_update.return_value = {"count": 1}
        self.challenge_records, self.session_records = {}, {}
        self.challenges.insert_one.side_effect = lambda record: self.challenge_records.update({record["_id"]: record})
        self.sessions.insert_one.side_effect = lambda record: self.session_records.update({record["_id"]: record})
        self.challenges.find_one.side_effect = lambda query, _: self.lookup(self.challenge_records, query)
        self.challenges.find_one_and_delete.side_effect = self.consume_challenge
        self.sessions.find_one.side_effect = lambda query, _: self.lookup(self.session_records, query)
        self.sessions.delete_one.side_effect = lambda query: self.session_records.pop(query["_id"], None)
        self.request = self.enterContext(patch("app.services.google_signin.Request"))
        self.request.return_value.return_value = SimpleNamespace(
            status=200, data=json.dumps({"test-key": self.public_key}).encode(),
        )

    @staticmethod
    def lookup(records, query):
        record = records.get(query["_id"])
        return record if record and record["expires_at"] > query["expires_at"]["$gt"] else None

    def consume_challenge(self, query):
        if self.lookup(self.challenge_records, query):
            return self.challenge_records.pop(query["_id"])
        return None

    def post(self, path, data=None, headers=HEADERS):
        return self.http.post(f"/api/auth/{path}", json=data, headers=headers, base_url=ORIGIN)

    def challenge(self):
        response = self.post("google/challenge")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Secure", response.headers["Set-Cookie"])
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertEqual(response.get_json()["client_id"], CLIENT_ID)
        return response.get_json()["nonce"]

    def credential(self, nonce, **overrides):
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": CLIENT_ID, "iat": now - 10, "exp": now + 300, "nonce": nonce,
            "sub": "google-subject", "email": "operator@example.net", "email_verified": True,
            "name": "Google Operator", **overrides,
        }
        return jwt.encode(self.signer, claims, key_id="test-key").decode()

    def test_signed_token_creates_user_session_without_role_escalation(self):
        credential = self.credential(self.challenge(), role="admin", hd="example.net")
        response = self.post("google", {"credential": credential, "role": "admin", "username": "operator"})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["Set-Cookie"]
        self.assertTrue(cookie.startswith(SESSION_COOKIE + "="))
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=14400"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertNotIn(credential, response.get_data(as_text=True))
        identity = {"role": "user", "name": "Google Operator", "email": "operator@example.net"}
        self.assertEqual(response.get_json()["user"], identity)
        self.assertFalse(self.challenge_records)
        self.assertNotIn(self.http.get_cookie(SESSION_COOKIE, domain="dashboard.example.net").value, self.session_records)
        changes = self.users.update_one.call_args
        self.assertEqual(changes.args[0], {"_id": "google-subject"})
        self.assertEqual(changes.args[1]["$set"], {**identity, "email_verified": True})
        self.assertIn("created_at", changes.args[1]["$setOnInsert"])
        self.assertTrue(changes.kwargs["upsert"])
        session = self.http.get("/api/auth/session", base_url=ORIGIN)
        account = session.get_json()
        self.assertIn("expires_at", account)
        self.assertEqual(account["user"], identity)
        self.assertEqual(account["csrf_token"], response.get_json()["csrf_token"])
        self.assertEqual(self.request.return_value.call_args.kwargs["timeout"], 5)
        self.assertEqual(self.request.return_value.call_args.args[0], "https://www.googleapis.com/oauth2/v1/certs")

        authenticated = {**HEADERS, "X-CSRF-Token": session.get_json()["csrf_token"]}
        for method, path in (("GET", "/api/admin/targets"), ("POST", "/api/targets"),
                             ("PATCH", "/api/targets/id"), ("DELETE", "/api/targets/id")):
            with self.subTest(method=method):
                forbidden = self.http.open(path, method=method, base_url=ORIGIN, headers=authenticated)
                self.assertEqual(forbidden.status_code, 403)

        self.assertEqual(self.post("logout").status_code, 403)
        self.assertEqual(self.post("logout", headers={**authenticated, "Origin": "https://other.example.net"}).status_code, 403)
        self.assertEqual(self.post("logout", headers=authenticated).status_code, 204)
        self.assertFalse(self.session_records)
        self.assertEqual(self.http.get("/api/auth/session", base_url=ORIGIN).status_code, 401)

    def test_invalid_signature_audience_issuer_expiry_email_and_nonce_do_not_sign_in(self):
        nonce = self.challenge()
        good = self.credential(nonce)
        head, payload, signature = good.split(".")
        changed_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        cases = (
            f"{head}.{payload}.{changed_signature}", self.credential(nonce, aud="other-client"),
            self.credential(nonce, iss="https://other.example.net"),
            self.credential(nonce, exp=int(time.time()) - 60),
            self.credential(nonce, email_verified=False), self.credential("incorrect-nonce"),
        )
        for index, invalid in enumerate(cases):
            with self.subTest(case=index):
                self.assertEqual(self.post("google", {"credential": invalid}).status_code, 401)
        self.assertFalse(self.session_records)
        self.users.update_one.assert_not_called()

    def test_replay_and_unbound_origin_are_rejected(self):
        self.assertEqual(self.post("google/challenge", headers={}).status_code, 403)
        nonce = self.challenge()
        challenge_cookie = self.http.get_cookie(CHALLENGE_COOKIE, domain="dashboard.example.net").value
        credential = self.credential(nonce)
        self.assertEqual(self.post("google", {"credential": credential}, headers={}).status_code, 403)
        self.assertEqual(self.post("google", {"credential": credential}).status_code, 200)
        self.http.set_cookie(CHALLENGE_COOKIE, challenge_cookie, domain="dashboard.example.net")
        self.assertEqual(self.post("google", {"credential": credential}).status_code, 401)
        self.users.update_one.assert_called_once()

    def test_disabled_and_rate_limited_google_signin(self):
        nonce = self.challenge()
        with patch("app.services.google_signin.consume_budget", side_effect=RateLimited(15)):
            response = self.post("google", {"credential": self.credential(nonce)})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "15")
        self.request.assert_not_called()
        self.app.config["GOOGLE_CLIENT_ID"] = None
        self.assertEqual(self.post("google/challenge").status_code, 503)
        self.assertEqual(self.post("google", {"credential": self.credential(nonce)}).status_code, 503)

    def test_google_session_expires_before_ttl_cleanup(self):
        self.assertEqual(self.post("google", {"credential": self.credential(self.challenge())}).status_code, 200)
        session = next(iter(self.session_records.values()))
        session["expires_at"] -= timedelta(hours=5)
        self.assertEqual(self.http.get("/api/auth/session", base_url=ORIGIN).status_code, 401)
