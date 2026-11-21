import os
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch

from pymongo.errors import AutoReconnect, DuplicateKeyError

import trigger_monitor
import triggered_monitor
from app import create_app
from app.services.limits import RateLimited
from worker_trigger_protocol import (
    BUSY, FAILED, HOST, NO_DUE, PATH, REPLAY, REQUEST_ID_HEADER, SAVED, SIGNATURE_HEADER, SKIPPED,
    SUBPROCESS_TIMEOUT, TIMESTAMP_HEADER, signature,
)


SECRET = "x" * 43
NOW = 1_800_000_000
REQUEST_ID = "a" * 32


class TriggerRouteTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"WORKER_TRIGGER_SECRET": SECRET, "ADMIN_API_TOKEN": ""}))
        self.enterContext(patch("app.worker_routes.time.time", return_value=NOW))
        self.launch = self.enterContext(patch("app.worker_routes.subprocess.run"))
        self.launch.return_value.returncode = SAVED
        self.http = create_app().test_client()

    def headers(self, timestamp=NOW, request_id=REQUEST_ID):
        return {
            TIMESTAMP_HEADER: str(timestamp), REQUEST_ID_HEADER: request_id,
            SIGNATURE_HEADER: signature(SECRET, timestamp, request_id, b""),
        }

    def test_signed_empty_request_launches_only_the_fixed_worker_with_deadline(self):
        response = self.http.post(PATH, headers=self.headers())
        self.assertEqual((response.status_code, response.get_json()), (200, {"status": "saved"}))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        args, options = self.launch.call_args
        self.assertTrue(args[0][2].endswith("triggered_monitor.py"))
        self.assertEqual(args[0][3:], [REQUEST_ID])
        self.assertEqual(options["timeout"], SUBPROCESS_TIMEOUT)
        self.assertEqual(options["stdout"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)

    def test_missing_bad_or_reused_secret_fails_closed(self):
        for env in (
            {"WORKER_TRIGGER_SECRET": ""}, {"WORKER_TRIGGER_SECRET": "short"},
            {"ADMIN_API_TOKEN": SECRET}, {"MONGODB_URI": f"mongodb://fixture:{SECRET}@example.net"},
        ):
            with self.subTest(env=env), patch.dict(os.environ, env):
                self.assertEqual(self.http.post(PATH, headers=self.headers()).status_code, 503)
        self.launch.assert_not_called()

    def test_rejects_malformed_tampered_expired_future_and_browser_requests(self):
        cases = (
            {}, {**self.headers(), SIGNATURE_HEADER: "0" * 64}, {**self.headers(), REQUEST_ID_HEADER: "oops"},
            self.headers(NOW - 121), self.headers(NOW + 121),
            {**self.headers(), "Origin": "https://dashboard.example.net"},
            {**self.headers(), "Sec-Fetch-Site": "same-origin"},
            {**self.headers(), TIMESTAMP_HEADER: str(NOW - 1)},
        )
        for headers in cases:
            self.assertEqual(self.http.post(PATH, headers=headers).status_code, 401)
        self.launch.assert_not_called()

    def test_rejects_any_body_and_chunked_requests(self):
        for body in (b"{}", b"x" * 8192):
            self.assertEqual(self.http.post(PATH, headers=self.headers(), data=body).status_code, 413)
        response = self.http.post(PATH, headers={**self.headers(), "Transfer-Encoding": "chunked"})
        self.assertEqual(response.status_code, 413)
        self.launch.assert_not_called()

    def test_worker_conflicts_failures_and_timeouts_are_sanitized(self):
        for exit_code, status, code in ((REPLAY, "replay", 409), (BUSY, "busy", 409),
                                       (NO_DUE, "no_due_targets", 200), (FAILED, "monitoring_failed", 503)):
            self.launch.return_value.returncode = exit_code
            response = self.http.post(PATH, headers=self.headers())
            self.assertEqual((response.status_code, response.get_json()), (code, {"status": status}))
        self.launch.side_effect = subprocess.TimeoutExpired("private command", SUBPROCESS_TIMEOUT)
        response = self.http.post(PATH, headers=self.headers())
        self.assertEqual((response.status_code, response.get_json()), (504, {"status": "deadline_exceeded"}))


class WorkerDispatchTests(unittest.TestCase):
    def setUp(self):
        client = self.enterContext(patch("app.database.get_mongo_client"))
        database = client.return_value.__enter__.return_value.__getitem__.return_value
        self.records = database.get_collection.return_value
        self.records.update_one.return_value.upserted_id = f"request:{REQUEST_ID}"
        self.records.find_one_and_update.side_effect = lambda _, update, **opts: {
            "owner": update[0]["$set"]["owner"]["$cond"][1],
        }
        self.due = self.enterContext(patch("monitor_worker.list_due_urls", return_value=["https://one.example.net", "https://two.example.net"]))
        self.check = self.enterContext(patch("monitor_worker.run_registered_check", return_value={"status": "offline"}))

    def test_one_target_uses_existing_dispatch_and_releases_owned_lease(self):
        self.assertEqual(triggered_monitor.dispatch(REQUEST_ID), SAVED)
        self.due.assert_called_once_with(1)
        self.check.assert_called_once_with("https://one.example.net")
        self.records.create_index.assert_called_once_with("expires_at", expireAfterSeconds=0)
        replay = self.records.update_one.call_args
        self.assertEqual(replay.args[0]["_id"], f"request:{REQUEST_ID}")
        self.assertTrue(replay.kwargs["upsert"])
        self.assertIn("$$NOW", str(replay.args[1]))
        lease = self.records.find_one_and_update.call_args
        self.assertTrue(lease.kwargs["upsert"])
        self.assertIn("$$NOW", str(lease.args[1]))
        self.records.delete_one.assert_called_once_with({"_id": "dispatch", "owner": REQUEST_ID})

    def test_replay_is_rejected_before_target_lookup(self):
        self.records.update_one.return_value.upserted_id = None
        self.assertEqual(triggered_monitor.dispatch(REQUEST_ID), REPLAY)
        self.records.update_one.side_effect = DuplicateKeyError("private credentials")
        self.assertEqual(triggered_monitor.dispatch(REQUEST_ID), REPLAY)
        self.records.find_one_and_update.assert_not_called()
        self.due.assert_not_called()
        self.check.assert_not_called()

    def test_overlapping_dispatch_cannot_probe_or_release_the_first_lease(self):
        running, finish = Event(), Event()

        def probe(url):
            running.set()
            if not finish.wait(3):
                raise RuntimeError("test timed out")
            return {"status": "online"}

        self.check.side_effect = probe
        self.records.find_one_and_update.side_effect = [{"owner": REQUEST_ID}, {"owner": REQUEST_ID}]
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(triggered_monitor.dispatch, REQUEST_ID)
            try:
                self.assertTrue(running.wait(3))
                second = pool.submit(triggered_monitor.dispatch, "b" * 32)
                self.assertEqual(second.result(timeout=3), BUSY)
                self.records.delete_one.assert_not_called()
            finally:
                finish.set()
            self.assertEqual(first.result(timeout=3), SAVED)
        self.check.assert_called_once()
        self.records.delete_one.assert_called_once_with({"_id": "dispatch", "owner": REQUEST_ID})

    def test_cooldown_and_probe_failure_keep_existing_outcomes(self):
        self.check.side_effect = [RateLimited(30), AutoReconnect("driver diagnostics")]
        self.assertEqual(triggered_monitor.dispatch(REQUEST_ID), SKIPPED)
        self.assertEqual(triggered_monitor.dispatch("b" * 32), FAILED)
        self.assertEqual(self.records.delete_one.call_count, 2)

    def test_worker_arms_os_watchdog_before_dispatch(self):
        with (
            patch.object(triggered_monitor.sys, "argv", ["triggered_monitor.py", REQUEST_ID]),
            patch.object(triggered_monitor.signal, "SIGALRM", 14, create=True),
            patch.object(triggered_monitor.signal, "alarm", create=True) as alarm,
            patch.object(triggered_monitor.signal, "signal"),
            patch.object(triggered_monitor, "dispatch") as dispatch,
        ):
            def run(_):
                alarm.assert_called_once_with(triggered_monitor.WORKER_ALARM)
                return NO_DUE
            dispatch.side_effect = run
            self.assertEqual(triggered_monitor.main(), NO_DUE)


class ActionsClientTests(unittest.TestCase):
    def test_tls_client_signs_empty_fixed_path_and_rejects_redirects(self):
        with patch.dict(os.environ, {"WORKER_TRIGGER_SECRET": SECRET}), patch("trigger_monitor.http.client.HTTPSConnection") as https:
            connection = https.return_value
            connection.getresponse.return_value = MagicMock(status=302)
            with patch("builtins.print"):
                self.assertEqual(trigger_monitor.main(), 1)
            https.assert_called_once_with(HOST, timeout=100)
            args, options = connection.request.call_args
            self.assertEqual(args, ("POST", PATH))
            self.assertEqual(options["body"], b"")
            headers = options["headers"]
            self.assertEqual(headers[SIGNATURE_HEADER], signature(SECRET, headers[TIMESTAMP_HEADER], headers[REQUEST_ID_HEADER], b""))
            connection.getresponse.return_value.read.assert_not_called()
