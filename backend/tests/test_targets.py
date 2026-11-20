import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import DEFAULT, MagicMock, patch

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app import create_app
from app.services.limits import GLOBAL_CHECKS_PER_WINDOW, RateLimited, consume_budget
from app.services.monitor import SecureTransportUnavailable, UnsafeTarget
from app.services.targets import list_due_urls


URL = "https://monitor.example.net/status"
ADMIN = {"Authorization": "Bearer test-admin"}


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.http = create_app().test_client()
        environment = patch.dict("os.environ", {"ADMIN_API_TOKEN": "test-admin"})
        environment.start()
        self.addCleanup(environment.stop)
        self.mongo = MagicMock()
        self.mock("app.services.targets.get_mongo_client", self.mongo)
        self.mock("app.services.monitoring.get_mongo_client", self.mongo)
        client = self.mongo.return_value.__enter__.return_value
        self.database = client.__getitem__.return_value
        self.targets = MagicMock()
        self.limits = MagicMock()
        self.database.__getitem__.side_effect = {
            "monitoring_targets": self.targets,
            "monitoring_rate_limits": self.limits,
        }.__getitem__
        self.targets.find_one.return_value = None
        self.target_id = ObjectId()
        self.targets.insert_one.return_value.inserted_id = self.target_id
        self.targets.update_one.return_value.matched_count = 1
        self.limits.find_one_and_update.return_value = {"count": 1}
        self.dns = self.mock("app.services.targets.validate_public_dns")
        self.check = self.mock("app.services.monitoring.check_website")
        self.save = self.mock("app.services.monitoring.save_check")

    def mock(self, name, value=DEFAULT):
        patcher = patch(name, value)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_admin_endpoints_fail_closed_without_valid_token(self):
        self.assertEqual(self.http.post("/api/targets", json={"url": URL}).status_code, 401)
        self.assertEqual(self.http.delete(f"/api/targets/{self.target_id}").status_code, 401)
        with patch.dict("os.environ", {"ADMIN_API_TOKEN": ""}):
            response = self.http.post("/api/targets", json={"url": URL}, headers=ADMIN)
        self.assertEqual(response.status_code, 401)
        self.mongo.assert_not_called()
        self.dns.assert_not_called()

    def test_registration_creates_unique_index_and_stores_canonical_url(self):
        response = self.http.post(
            "/api/targets", json={"url": "https://MONITOR.EXAMPLE.NET:443/status"}, headers=ADMIN
        )
        self.assertEqual(response.status_code, 201)
        result = response.get_json()
        self.assertEqual((result["id"], result["url"], result["enabled"]), (str(self.target_id), URL, True))
        self.assertTrue(result["created_at"].endswith("Z"))
        self.targets.create_index.assert_called_once_with("url", unique=True)
        self.limits.create_index.assert_called_once_with("expires_at", expireAfterSeconds=0)
        self.dns.assert_called_once_with(URL)
        record = self.targets.insert_one.call_args.args[0]
        self.assertIsInstance(record["created_at"], datetime)
        self.assertEqual(record["created_at"].utcoffset(), timedelta(0))

    def test_duplicate_registration_handles_concurrent_insert(self):
        self.targets.insert_one.side_effect = DuplicateKeyError("private driver details")
        response = self.http.post("/api/targets", json={"url": URL}, headers=ADMIN)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json(), {"error": "Target is already registered"})

    def test_registration_rejects_unsafe_dns_answer_without_inserting(self):
        self.dns.side_effect = UnsafeTarget()
        response = self.http.post("/api/targets", json={"url": URL}, headers=ADMIN)
        self.assertEqual(response.status_code, 400)
        self.targets.insert_one.assert_not_called()

    def test_registration_is_rate_limited_before_dns(self):
        self.limits.find_one_and_update.return_value = {"count": 11}
        response = self.http.post("/api/targets", json={"url": URL}, headers=ADMIN)
        self.assertEqual(response.status_code, 429)
        self.dns.assert_not_called()
        self.targets.insert_one.assert_not_called()

    def test_list_and_disable_use_enabled_flag_and_keep_record(self):
        self.targets.find.return_value = [{
            "_id": self.target_id, "url": URL, "enabled": True,
            "created_at": datetime(2026, 11, 20, tzinfo=timezone.utc),
        }]
        response = self.http.get("/api/targets")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()[0]["url"], URL)
        self.targets.find.assert_called_once_with({"enabled": True}, sort=[("url", 1)])

        response = self.http.delete(f"/api/targets/{self.target_id}", headers=ADMIN)
        self.assertEqual(response.status_code, 204)
        self.targets.update_one.assert_called_once_with(
            {"_id": self.target_id}, {"$set": {"enabled": False}}
        )
        self.targets.delete_one.assert_not_called()

    def test_unregistered_target_cannot_reach_outbound_check(self):
        response = self.http.post("/api/check", json={"url": URL})
        self.assertEqual(response.status_code, 400)
        self.targets.find_one.assert_called_once_with({"url": URL, "enabled": True}, {"_id": 1})
        self.limits.find_one_and_update.assert_not_called()
        self.check.assert_not_called()
        self.save.assert_not_called()

    def test_global_rate_limit_prevents_outbound_check(self):
        self.targets.find_one.return_value = {"_id": self.target_id}
        self.limits.find_one_and_update.side_effect = [{"count": 1}, {"count": GLOBAL_CHECKS_PER_WINDOW + 1}]
        response = self.http.post("/api/check", json={"url": URL})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        self.targets.update_one.assert_not_called()
        self.check.assert_not_called()

    def test_atomic_target_cooldown_prevents_outbound_check(self):
        self.targets.find_one.return_value = {"_id": self.target_id}
        self.targets.update_one.return_value.matched_count = 0
        response = self.http.post("/api/check", json={"url": URL})
        self.assertEqual(response.status_code, 429)
        filters, update = self.targets.update_one.call_args.args
        self.assertEqual(filters["_id"], self.target_id)
        self.assertTrue(filters["enabled"])
        cutoff = filters["$or"][0]["next_check_at"]["$lte"]
        self.assertGreater(update["$set"]["next_check_at"], cutoff)
        self.check.assert_not_called()
        self.save.assert_not_called()

    def test_unsafe_revalidation_and_missing_transport_do_not_save(self):
        self.targets.find_one.return_value = {"_id": self.target_id}
        for error, code in ((UnsafeTarget(), 400), (SecureTransportUnavailable(), 503)):
            with self.subTest(code=code):
                self.check.side_effect = error
                response = self.http.post("/api/check", json={"url": URL})
                self.assertEqual(response.status_code, code)
        self.save.assert_not_called()

    def test_worker_lists_bounded_batch_of_enabled_due_targets(self):
        self.targets.find.return_value = [{"url": URL}]
        self.assertEqual(list_due_urls(10), [URL])
        filters, projection = self.targets.find.call_args.args
        self.assertTrue(filters["enabled"])
        self.assertIn("$or", filters)
        self.assertEqual(projection, {"url": 1, "_id": 0})
        self.assertEqual(self.targets.find.call_args.kwargs["limit"], 10)

    def test_budget_recovers_from_concurrent_bucket_creation(self):
        self.limits.find_one_and_update.side_effect = [DuplicateKeyError("private"), {"count": 2}]
        now = datetime(2026, 11, 20, tzinfo=timezone.utc)
        with self.assertRaises(RateLimited):
            consume_budget(self.database, "checks", 1, now)
        first, retry = self.limits.find_one_and_update.call_args_list
        self.assertEqual(first.args, retry.args)
        self.assertTrue(first.kwargs["upsert"])
        self.assertEqual(retry.kwargs, {"return_document": ReturnDocument.AFTER})
