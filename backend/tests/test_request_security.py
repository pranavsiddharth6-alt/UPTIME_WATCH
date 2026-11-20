import os
import unittest
from io import BytesIO
from unittest.mock import patch

from app import create_app


class RequestSecurityTests(unittest.TestCase):
    def setUp(self):
        self.origin = "https://dashboard.example.net"
        with patch.dict(os.environ, {
            "UPTIMEWATCH_ENV": "production", "UPTIMEWATCH_FRONTEND_ORIGIN": self.origin,
        }):
            self.http = create_app().test_client()

    def test_oversized_mutations_fail_before_auth_or_service_calls(self):
        cases = (
            ("POST", "/api/check", 16385),
            ("POST", "/api/check-once", 16385),
            ("POST", "/api/targets", 16385),
            ("PATCH", "/api/targets/id", 1025),
            ("DELETE", "/api/targets/id", 1),
            ("POST", "/api/admin/login", 8193),
            ("POST", "/api/admin/logout", 1),
        )
        with (
            patch("app.routes.run_registered_check") as check,
            patch("app.routes.run_one_time_check") as check_once,
            patch("app.admin_routes.admin.session_username") as session,
            patch("app.admin_routes.admin.login") as login,
        ):
            for method, path, size in cases:
                with self.subTest(method=method, path=path):
                    response = self.http.open(path, method=method, data=b"x" * size)
                    self.assertEqual(response.status_code, 413)
                    self.assertTrue(response.is_json)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            for service in (check, check_once, session, login):
                service.assert_not_called()

    def test_oversized_stream_is_rejected_even_if_prefix_is_valid_json(self):
        prefix = b'{"url":"https://example.net"}'.ljust(16384)
        with patch("app.routes.run_registered_check") as check:
            response = self.http.post("/api/check", content_type="application/json", environ_overrides={
                "CONTENT_LENGTH": "", "wsgi.input_terminated": True, "wsgi.input": BytesIO(prefix + b"x"),
            })
        self.assertEqual(response.status_code, 413)
        check.assert_not_called()

    def test_invalid_json_returns_public_error(self):
        invalid = (
            (b"{", "application/json"),
            (b"[]", "application/json"),
            (b'{"url":"https://example.net"}', "text/plain"),
        )
        for payload, content_type in invalid:
            with self.subTest(payload=payload, content_type=content_type):
                response = self.http.post("/api/check", data=payload, content_type=content_type)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.is_json)

    def test_production_uses_shared_identity_for_peers_and_forwarded_headers(self):
        with patch("app.routes.run_registered_check", return_value={}) as check:
            for address in ("192.0.2.10", "192.0.2.11"):
                response = self.http.post(
                    "/api/check", json={"url": "https://example.net"},
                    environ_overrides={"REMOTE_ADDR": address}, headers={
                        "X-Forwarded-For": "198.51.100.5", "Forwarded": "for=203.0.113.1",
                    },
                )
                self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [call.kwargs["client_address"] for call in check.call_args_list],
            ["production-shared", "production-shared"],
        )

    def test_local_development_login_uses_localhost_origin(self):
        with patch.dict(os.environ, {"UPTIMEWATCH_ENV": "development", "UPTIMEWATCH_FRONTEND_ORIGIN": ""}):
            http = create_app().test_client()
        origin = "http://localhost:5173"
        with patch("app.admin_routes.admin.login", return_value="mock-session") as login:
            response = http.post(
                "/api/admin/login", base_url=origin, headers={"Origin": origin},
                json={"username": "operator", "password": "mock-password"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(login.call_args.args[2], "127.0.0.1")
        cookie = response.headers["Set-Cookie"]
        self.assertTrue(cookie.startswith("uptimewatch_admin="))
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Secure", cookie)
