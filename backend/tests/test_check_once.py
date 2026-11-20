import unittest
from hashlib import sha256
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pymongo.errors import AutoReconnect

from app import create_app
from app.services.limits import ONE_TIME_CHECKS_PER_WINDOW, ONE_TIME_CLIENT_CHECKS_PER_WINDOW
from app.services.monitor import SecureTransportUnavailable


URL = "https://github.com"
PUBLIC_IP = "140.82.112.3"


class OneTimeCheckTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config["TESTING"] = True
        self.app = app
        self.http = app.test_client()
        self.mongo = self.start_patch("app.services.monitoring.get_mongo_client")
        client = self.mongo.return_value.__enter__.return_value
        self.database = client.__getitem__.return_value
        self.buckets = MagicMock()
        self.database.__getitem__.side_effect = {"monitoring_rate_limits": self.buckets}.__getitem__
        self.buckets.find_one_and_update.return_value = {"count": 1}
        self.save = self.start_patch("app.services.monitoring.save_check")
        self.register = self.start_patch("app.routes.register_target")
        self.resolve = self.start_patch("app.services.monitor.dns.resolver.resolve")
        self.resolve.side_effect = [[SimpleNamespace(address=PUBLIC_IP)], []]
        self.probe = self.start_patch("app.services.monitor._probe_https")
        self.probe.return_value = 200

    def start_patch(self, name):
        patcher = patch(name)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_unregistered_public_url_checks_and_uses_only_rate_limit_storage(self):
        response = self.http.post("/api/check-once", json={"url": "https://GITHUB.COM:443/"})

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["url"], URL)
        self.assertEqual((result["status"], result["http_status"]), ("online", 200))
        self.assertGreaterEqual(result["response_time_ms"], 0)
        self.assertEqual(set(result), {"url", "status", "http_status", "response_time_ms"})
        self.buckets.create_index.assert_called_once_with("expires_at", expireAfterSeconds=0)
        self.assertEqual(self.buckets.find_one_and_update.call_count, 2)
        self.assertEqual(
            self.probe.call_args.args[1:4], (URL, "github.com", ip_address(PUBLIC_IP))
        )
        self.save.assert_not_called()
        self.register.assert_not_called()

    def test_rejects_malformed_or_unsafe_url_syntax_before_dns_and_mongo(self):
        payloads = (
            {}, {"url": []}, {"url": "http://github.com"}, {"url": "https://127.0.0.1"},
            {"url": "https://[::1]"}, {"url": "https://host.internal"},
            {"url": "https://user:password@github.com"}, {"url": URL + "?secret"},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.http.post("/api/check-once", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("public HTTPS URL", response.get_json()["error"])
        self.mongo.assert_not_called()
        self.resolve.assert_not_called()
        self.probe.assert_not_called()

    def test_oversized_request_rejected_before_mongo_or_dns(self):
        response = self.http.post("/api/check-once", json={"url": URL, "extra": "x" * 20000})
        self.assertEqual(response.status_code, 413)
        self.mongo.assert_not_called()
        self.resolve.assert_not_called()

    def test_mixed_public_and_private_dns_answers_fail_closed(self):
        self.resolve.side_effect = [
            [SimpleNamespace(address=PUBLIC_IP)], [SimpleNamespace(address="fd00::1")],
        ]
        response = self.http.post("/api/check-once", json={"url": URL})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("fd00::1", response.get_data(as_text=True))
        self.probe.assert_not_called()
        self.save.assert_not_called()

    def test_client_limit_uses_peer_address_and_blocks_dns(self):
        self.app.config["USE_DIRECT_CLIENT_ADDRESS"] = True
        self.buckets.find_one_and_update.return_value = {"count": ONE_TIME_CLIENT_CHECKS_PER_WINDOW + 1}
        response = self.http.post(
            "/api/check-once", json={"url": URL}, headers={"X-Forwarded-For": "203.0.113.1"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.buckets.find_one_and_update.assert_called_once()
        bucket = self.buckets.find_one_and_update.call_args.args[0]["_id"]
        self.assertTrue(bucket.startswith(f"once:client:{sha256(b'127.0.0.1').hexdigest()}:"))
        self.resolve.assert_not_called()

    def test_global_limit_is_separate_from_registered_checks(self):
        self.buckets.find_one_and_update.side_effect = [
            {"count": 1}, {"count": ONE_TIME_CHECKS_PER_WINDOW + 1},
        ]
        response = self.http.post("/api/check-once", json={"url": URL})
        self.assertEqual(response.status_code, 429)
        bucket = self.buckets.find_one_and_update.call_args.args[0]["_id"]
        self.assertTrue(bucket.startswith("once:checks:"))
        self.resolve.assert_not_called()

    def test_database_failure_blocks_dns_and_sanitizes_response(self):
        self.buckets.find_one_and_update.side_effect = AutoReconnect("private connection details")
        response = self.http.post("/api/check-once", json={"url": URL})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private connection details", response.get_data(as_text=True))
        self.resolve.assert_not_called()
        self.probe.assert_not_called()

    def test_secure_transport_unavailable_never_falls_back(self):
        with patch("app.services.monitor._tls_context", side_effect=SecureTransportUnavailable()):
            response = self.http.post("/api/check-once", json={"url": URL})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"error": "Secure website checker unavailable."})
        self.resolve.assert_not_called()
        self.probe.assert_not_called()

    def test_timeout_returns_offline_without_writing_history(self):
        self.probe.side_effect = TimeoutError("private timeout details")
        response = self.http.post("/api/check-once", json={"url": URL})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "offline")
        self.assertIsNone(response.get_json()["http_status"])
        self.assertNotIn("private timeout details", response.get_data(as_text=True))
        self.save.assert_not_called()

    def test_redirect_is_reported_without_following(self):
        self.probe.return_value = 302
        response = self.http.post("/api/check-once", json={"url": URL})
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual((result["status"], result["http_status"]), ("offline", 302))
        self.probe.assert_called_once()
