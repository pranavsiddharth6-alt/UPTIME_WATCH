import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from pymongo.errors import DuplicateKeyError

from app import create_app
from app.services import admin
from app.services.limits import RateLimited


ORIGIN = "https://app.example.com"
API = "https://api.example.com"
PASSWORD = "test-only strong passphrase"
HEADERS = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-site"}
COOKIE = "__Host-uptimewatch_admin"


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = admin.hash_password(PASSWORD)

    def setUp(self):
        environment = patch.dict("os.environ", {
            "UPTIMEWATCH_ENV": "production",
            "UPTIMEWATCH_FRONTEND_ORIGIN": ORIGIN,
            "ADMIN_API_TOKEN": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        app = create_app()
        app.config["TESTING"] = True
        self.http = app.test_client()

        self.mongo = self.mock("app.services.admin.get_mongo_client")
        client = self.mongo.return_value.__enter__.return_value
        database = client.__getitem__.return_value
        self.accounts, self.sessions, buckets = (MagicMock(), MagicMock(), MagicMock())
        database.__getitem__.side_effect = {
            "admin_accounts": self.accounts, "admin_sessions": self.sessions,
            "monitoring_rate_limits": buckets,
        }.__getitem__
        buckets.find_one_and_update.return_value = {"count": 1}
        self.accounts.find_one.return_value = {"username": "operator", "password_hash": self.password_hash}

        self.saved_sessions = {}
        self.sessions.insert_one.side_effect = self.store_session
        self.sessions.find_one.side_effect = self.find_session
        self.sessions.delete_one.side_effect = self.delete_session
        self.targets = self.mock("app.admin_routes.list_targets")
        self.targets.return_value = []
        self.register = self.mock("app.routes.register_target")
        self.register.return_value = {"url": "https://github.com"}
        self.toggle = self.mock("app.routes.set_target_enabled")
        self.disable = self.mock("app.routes.disable_target")

    def mock(self, name):
        patcher = patch(name)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def store_session(self, document):
        self.saved_sessions[document["_id"]] = dict(document)

    def find_session(self, filters, projection):
        value = self.saved_sessions.get(filters["_id"])
        if value and value["expires_at"] > filters["expires_at"]["$gt"]:
            return {"username": value["username"]}
        return None

    def delete_session(self, filters):
        self.saved_sessions.pop(filters["_id"], None)

    def sign_in(self, username="operator", password=PASSWORD, headers=HEADERS):
        return self.http.post(
            "/api/admin/login", json={"username": username, "password": password},
            headers=headers, base_url=API,
        )

    def test_bootstrap_stores_only_a_salted_hash_and_refuses_second_account(self):
        admin.create_admin("operator", PASSWORD)
        document = self.accounts.insert_one.call_args.args[0]
        self.assertEqual(document["_id"], "admin")
        self.assertNotIn("password", document)
        self.assertTrue(admin._verify_password(document["password_hash"], PASSWORD))
        self.assertNotEqual(document["password_hash"], self.password_hash)
        self.accounts.insert_one.side_effect = DuplicateKeyError("private details")
        with self.assertRaises(admin.AdminAlreadyExists):
            admin.create_admin("other-admin", PASSWORD)

    def test_login_sets_secure_cookie_and_stores_only_session_digest(self):
        response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(response.headers["Access-Control-Allow-Credentials"], "true")
        header = response.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=14400"):
            self.assertIn(flag, header)
        self.assertNotIn("Domain=", header)
        cookie = self.http.get_cookie(COOKIE, domain="api.example.com")
        self.assertIsNotNone(cookie)
        self.assertTrue(cookie.value not in self.saved_sessions)
        self.assertTrue(cookie.value not in response.get_data(as_text=True))
        self.assertEqual(len(self.saved_sessions), 1)
        self.sessions.create_index.assert_called_once_with("expires_at", expireAfterSeconds=0)
        session = self.http.get("/api/admin/session", headers=HEADERS, base_url=API)
        self.assertEqual(session.get_json(), response.get_json())

    def test_login_rotates_session_and_expiry_is_enforced_before_ttl_cleanup(self):
        self.assertEqual(self.sign_in().status_code, 200)
        original_id = next(iter(self.saved_sessions))
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertNotIn(original_id, self.saved_sessions)
        record = next(iter(self.saved_sessions.values()))
        record["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.http.get("/api/admin/session", base_url=API).status_code, 401)
        self.assertEqual(self.http.get("/api/admin/targets", base_url=API).status_code, 401)
        self.targets.assert_not_called()

    def test_login_errors_are_generic_and_rate_limit_precedes_verification(self):
        wrong_user = self.sign_in(username="other")
        wrong_password = self.sign_in(password="incorrect")
        self.assertEqual((wrong_user.status_code, wrong_password.status_code), (401, 401))
        self.assertEqual(wrong_user.get_json(), wrong_password.get_json())
        self.accounts.find_one.reset_mock()
        with patch("app.services.admin.consume_budget", side_effect=RateLimited(15)):
            limited = self.sign_in()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["Retry-After"], "15")
        self.accounts.find_one.assert_not_called()

    def test_login_rejects_missing_untrusted_or_cross_site_origin(self):
        for headers in ({}, {"Origin": "https://wrong.example.com"}, {**HEADERS, "Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                response = self.sign_in(headers=headers)
                self.assertEqual(response.status_code, 403)
        self.mongo.assert_not_called()

    def test_admin_target_listing_and_mutations_require_session_and_csrf(self):
        target_path = "/api/targets/0123456789abcdef01234567"
        self.assertEqual(self.http.get("/api/admin/targets", base_url=API).status_code, 401)
        self.assertEqual(self.http.patch(target_path, json={"enabled": True}, base_url=API).status_code, 401)
        csrf = self.sign_in().get_json()["csrf_token"]
        authorized = {**HEADERS, "X-CSRF-Token": csrf}
        listing = self.http.get("/api/admin/targets", headers=HEADERS, base_url=API)
        self.assertEqual(listing.status_code, 200)
        self.targets.assert_called_once_with(include_disabled=True)
        self.assertEqual(self.http.patch(target_path, json={"enabled": True}, headers=HEADERS, base_url=API).status_code, 403)
        self.assertEqual(self.http.delete(target_path, headers={**authorized, "Origin": "https://wrong.example.com"}, base_url=API).status_code, 401)
        self.toggle.assert_not_called()
        self.disable.assert_not_called()
        self.assertEqual(self.http.patch(target_path, json={"enabled": "true"}, headers=authorized, base_url=API).status_code, 400)
        self.assertEqual(self.http.patch(target_path, json={"enabled": True}, headers=authorized, base_url=API).status_code, 204)
        self.assertEqual(self.http.patch(target_path, json={"enabled": False}, headers=authorized, base_url=API).status_code, 204)
        self.assertEqual([call.args[1] for call in self.toggle.call_args_list], [True, False])
        self.assertEqual(self.http.post("/api/targets", json={"url": "https://github.com"}, headers=authorized, base_url=API).status_code, 201)
        self.register.assert_called_once_with("https://github.com")

    def test_logout_requires_csrf_and_revokes_server_session(self):
        csrf = self.sign_in().get_json()["csrf_token"]
        self.assertEqual(self.http.post("/api/admin/logout", headers=HEADERS, base_url=API).status_code, 403)
        self.assertEqual(len(self.saved_sessions), 1)
        response = self.http.post("/api/admin/logout", headers={**HEADERS, "X-CSRF-Token": csrf}, base_url=API)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.saved_sessions)
        self.assertIsNone(self.http.get_cookie(COOKIE, domain="api.example.com"))

    def test_bearer_token_allowed_for_cli_only(self):
        bearer = {"Authorization": "Bearer test-cli-token"}
        with patch.dict("os.environ", {"ADMIN_API_TOKEN": "test-cli-token"}):
            browser = self.http.get("/api/admin/targets", headers={**bearer, **HEADERS}, base_url=API)
            cli = self.http.get("/api/admin/targets", headers=bearer, base_url=API)
        self.assertEqual((browser.status_code, cli.status_code), (401, 200))

    def test_cors_preflight_uses_exact_origin_and_credentials(self):
        headers = {
            **HEADERS, "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Content-Type, X-CSRF-Token",
        }
        allowed = self.http.options("/api/targets/demo", headers=headers, base_url=API)
        denied = self.http.options("/api/targets/demo", headers={**headers, "Origin": ORIGIN + ".example.net"}, base_url=API)
        self.assertEqual(allowed.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(allowed.headers["Access-Control-Allow-Credentials"], "true")
        self.assertIn("X-CSRF-Token", allowed.headers["Access-Control-Allow-Headers"])
        self.assertNotIn("Access-Control-Allow-Origin", denied.headers)

    def test_unconfigured_production_origin_disables_browser_admin(self):
        with patch.dict("os.environ", {"UPTIMEWATCH_FRONTEND_ORIGIN": "http://app.example.com"}):
            http = create_app().test_client()
        response = http.post("/api/admin/login", json={"username": "operator", "password": PASSWORD}, headers=HEADERS)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.mongo.assert_not_called()

    def test_mongo_failure_is_sanitized_and_public_health_still_works(self):
        self.mongo.side_effect = RuntimeError("private connection details")
        response = self.sign_in()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private connection details", response.get_data(as_text=True))
        self.assertEqual(self.http.get("/health", base_url=API).get_json()["status"], "healthy")
