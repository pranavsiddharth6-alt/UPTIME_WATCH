import socket
import ssl
import unittest
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import dns.resolver

from app.services import monitor


URL = "https://public.example.net/status"
HOST = "public.example.net"
PUBLIC_IP = "93.184.215.14"


class SecureMonitorTests(unittest.TestCase):
    def setUp(self):
        dns_mock = patch("app.services.monitor.dns.resolver.resolve")
        self.resolve = dns_mock.start()
        self.addCleanup(dns_mock.stop)
        probe_mock = patch("app.services.monitor._probe_https")
        self.probe = probe_mock.start()
        self.addCleanup(probe_mock.stop)
        self.resolve.side_effect = [[SimpleNamespace(address=PUBLIC_IP)], []]

    def test_canonical_https_url_and_optional_path(self):
        self.assertEqual(monitor.normalize_url("HTTPS://PUBLIC.EXAMPLE.NET:443/"), "https://public.example.net")
        self.assertEqual(monitor.normalize_url(URL), URL)

    def test_rejects_unsafe_url_syntax_without_dns_or_http(self):
        urls = (
            None, {}, "http://public.example.net", "https://127.0.0.1", "https://[::1]",
            "https://local.internal", "https://a.local", "https://0x7f.0.0.1",
            "https://username@public.example.net", "https://public.example.net:8443",
            "https://public.example.net?", "https://public.example.net#",
            "https://public.example.net\\path", "https://public.example.net/%zz",
            "https://public.example.net/\r\n",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(monitor.UnsafeTarget):
                monitor.check_website(url)
        self.resolve.assert_not_called()
        self.probe.assert_not_called()

    def test_rejects_any_non_public_answer_before_https(self):
        blocked_addresses = (
            "10.0.0.9", "127.0.0.1", "169.254.169.254", "100.64.0.1",
            "168.63.129.16", "224.0.0.1", "fd00::1", "::ffff:127.0.0.1",
            "64:ff9b::a00:1", "2001:db8::1",
        )
        for blocked in blocked_addresses:
            with self.subTest(address=blocked):
                self.resolve.side_effect = [
                    [SimpleNamespace(address=PUBLIC_IP)],
                    [SimpleNamespace(address=blocked)],
                ]
                with self.assertRaises(monitor.UnsafeTarget):
                    monitor.check_website(URL)
        self.probe.assert_not_called()

    def test_validated_dns_address_is_passed_to_the_transport_once(self):
        self.probe.return_value = 204
        result = monitor.check_website(URL)

        self.assertEqual(result["status"], "online")
        self.assertEqual(result["http_status"], 204)
        self.assertEqual(self.resolve.call_count, 2)
        for dns_call, record_type in zip(self.resolve.call_args_list, ("A", "AAAA")):
            self.assertEqual(dns_call.args, (HOST + ".", record_type))
            self.assertFalse(dns_call.kwargs["search"])
            self.assertGreater(dns_call.kwargs["lifetime"], 0)
            self.assertLessEqual(dns_call.kwargs["lifetime"], monitor.DNS_SECONDS)
        self.probe.assert_called_once()
        self.assertEqual(self.probe.call_args.args[1:4], (URL, HOST, ip_address(PUBLIC_IP)))
        self.assertGreaterEqual(result["response_time_ms"], 0)

    def test_dns_timeout_for_one_family_does_not_use_other_address(self):
        self.resolve.side_effect = [
            [SimpleNamespace(address=PUBLIC_IP)], dns.resolver.LifetimeTimeout(),
        ]
        result = monitor.check_website(URL)
        self.assertEqual(result["status"], "offline")
        self.assertIsNone(result["http_status"])
        self.probe.assert_not_called()

    def test_redirect_is_offline_without_retry(self):
        self.probe.return_value = 302
        result = monitor.check_website(URL)
        self.assertEqual((result["status"], result["http_status"]), ("offline", 302))
        self.probe.assert_called_once()

    def test_tls_failure_is_offline_without_retry(self):
        self.probe.side_effect = ssl.SSLCertVerificationError("private certificate details")
        result = monitor.check_website(URL)
        self.assertEqual((result["status"], result["http_status"]), ("offline", None))
        self.probe.assert_called_once()


class PinnedTransportTests(unittest.TestCase):
    def test_transport_connects_to_numeric_ip_and_preserves_tls_hostname(self):
        endpoint = (PUBLIC_IP, 443)
        context = MagicMock(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)
        tls = context.wrap_socket.return_value.__enter__.return_value
        tls.recv.side_effect = [
            b"HTTP/1.1 103 Early Hints\r\n\r\nHTTP/1.1 302 Found\r\n",
            b"Location: https://redirect.example.net/\r\n\r\n",
        ]
        with (
            patch("app.services.monitor.socket.socket") as socket_factory,
            patch("app.services.monitor.socket.getaddrinfo") as address_lookup,
        ):
            address_lookup.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", endpoint)]
            status = monitor._probe_https(
                context, URL, HOST, ip_address(PUBLIC_IP), monitor.time.perf_counter() + 2
            )

        address_lookup.assert_called_once_with(
            PUBLIC_IP, 443, socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
            socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
        )
        raw = socket_factory.return_value.__enter__.return_value
        raw.connect.assert_called_once_with(endpoint)
        context.wrap_socket.assert_called_once_with(raw, server_hostname=HOST)
        tls.sendall.assert_called_once()
        self.assertIn(b"GET /status HTTP/1.1\r\nHost: public.example.net\r\n", tls.sendall.call_args.args[0])
        self.assertEqual(tls.recv.call_count, 2)
        self.assertEqual(status, 302)

    def test_slow_headers_share_one_deadline(self):
        connection = MagicMock()
        connection.recv.return_value = b"HTTP/1.1 200 "
        with patch("app.services.monitor.time.perf_counter", side_effect=[10, 11.1]):
            with self.assertRaises(monitor.ProbeUnavailable):
                monitor._read_http_status(connection, 11)
        self.assertEqual(connection.settimeout.call_args_list, [call(1)])
        connection.recv.assert_called_once()

    def test_header_size_is_bounded(self):
        connection = MagicMock()
        connection.recv.return_value = b"x" * 8
        with patch("app.services.monitor.MAX_HEADER_BYTES", 8):
            with self.assertRaises(monitor.ProbeUnavailable):
                monitor._read_http_status(connection, monitor.time.perf_counter() + 1)
        connection.recv.assert_called_once_with(8)

    def test_tls_context_requires_host_and_certificate_checks(self):
        context = monitor._tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_transport_rejects_unverified_tls_before_socket_creation(self):
        context = MagicMock(check_hostname=False, verify_mode=ssl.CERT_REQUIRED)
        with patch("app.services.monitor.socket.getaddrinfo") as lookup:
            with self.assertRaises(monitor.SecureTransportUnavailable):
                monitor._probe_https(
                    context, URL, HOST, ip_address(PUBLIC_IP), monitor.time.perf_counter() + 2
                )
        lookup.assert_not_called()
