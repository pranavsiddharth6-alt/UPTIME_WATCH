import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import call, patch

from pymongo.errors import AutoReconnect

import monitor_worker
from app.services.limits import RateLimited
from app.services.monitor import UnsafeTarget


WEBSITES = ["https://one.example.net/status", "https://two.example.net/health"]


class MonitorWorkerTests(unittest.TestCase):
    def setUp(self):
        self.load_env = self.start_patch("monitor_worker.load_dotenv")
        self.list_due = self.start_patch("monitor_worker.list_due_urls")
        self.list_due.return_value = WEBSITES
        self.check = self.start_patch("monitor_worker.run_registered_check")
        self.check.return_value = {"status": "online"}

    def start_patch(self, name):
        patcher = patch(name)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def run_worker(self):
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = monitor_worker.main()
        return exit_code, stdout.getvalue().splitlines()

    def test_checks_dynamic_targets_once_with_shared_dispatch(self):
        self.check.side_effect = [{"status": "online"}, {"status": "offline"}]
        self.assertEqual(self.run_worker(), (0, [
            "one.example.net: online (saved)", "two.example.net: offline (saved)",
        ]))
        self.load_env.assert_called_once_with(
            Path(monitor_worker.__file__).resolve().with_name(".env"), override=False
        )
        self.list_due.assert_called_once_with(monitor_worker.MAX_TARGETS_PER_RUN)
        self.assertEqual(self.check.call_args_list, [call(WEBSITES[0]), call(WEBSITES[1])])

    def test_skips_rate_limited_target_and_continues(self):
        self.check.side_effect = [RateLimited(20), {"status": "online"}]
        self.assertEqual(self.run_worker(), (0, [
            "one.example.net: Skipped (rate-limited or disabled)",
            "two.example.net: online (saved)",
        ]))
        self.assertEqual(self.check.call_count, 2)

    def test_sanitizes_database_and_safety_failures(self):
        self.check.side_effect = [AutoReconnect("private details"), UnsafeTarget()]
        self.assertEqual(self.run_worker(), (1, [
            "one.example.net: Monitoring database unavailable",
            "two.example.net: Website destination blocked",
        ]))
        self.assertEqual(self.check.call_count, 2)

    def test_no_due_targets(self):
        self.list_due.return_value = []
        self.assertEqual(self.run_worker(), (0, ["No monitoring targets are due"]))
        self.check.assert_not_called()
