import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError

from app import create_app


EXAMPLE_URL = "https://example.com"
PYTHON_URL = "https://www.python.org"


class MonitoringHistoryTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.config["TESTING"] = True
        self.http = app.test_client()

        config = patch.dict("os.environ", {"MONGODB_DB_NAME": "uptimewatch"})
        config.start()
        self.addCleanup(config.stop)

        mongo_mock = patch("app.services.history.get_mongo_client")
        self.mongo = mongo_mock.start()
        self.addCleanup(mongo_mock.stop)
        self.mongo_client = self.mongo.return_value.__enter__.return_value
        self.database = self.mongo_client.__getitem__.return_value
        self.collection = MagicMock()
        self.targets = MagicMock()
        self.limits = MagicMock()
        self.database.__getitem__.side_effect = {
            "monitoring_checks": self.collection,
            "monitoring_targets": self.targets,
            "monitoring_rate_limits": self.limits,
        }.__getitem__
        self.targets.find_one.return_value = {"_id": ObjectId()}
        self.targets.update_one.return_value.matched_count = 1
        self.targets.find.return_value = []
        self.limits.find_one_and_update.return_value = {"count": 1}
        connection_mock = patch("app.services.monitoring.get_mongo_client", self.mongo)
        connection_mock.start()
        self.addCleanup(connection_mock.stop)

        check_mock = patch("app.services.monitoring.check_website")
        self.check_website = check_mock.start()
        self.addCleanup(check_mock.stop)
        self.check_website.return_value = {
            "url": EXAMPLE_URL,
            "status": "offline",
            "http_status": None,
            "response_time_ms": 123.45,
        }

    def test_check_saves_utc_datetime_and_preserves_response(self):
        def assign_id(document):
            document["_id"] = ObjectId()

        self.collection.insert_one.side_effect = assign_id
        response = self.http.post("/api/check", json={"url": EXAMPLE_URL})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), self.check_website.return_value)
        self.check_website.assert_called_once_with(EXAMPLE_URL)
        self.mongo_client.__getitem__.assert_any_call("uptimewatch")
        self.database.__getitem__.assert_any_call("monitoring_checks")
        self.collection.insert_one.assert_called_once()
        document = self.collection.insert_one.call_args.args[0]
        self.assertEqual(
            {key: document[key] for key in response.get_json()}, response.get_json()
        )
        self.assertIsInstance(document["checked_at"], datetime)
        self.assertEqual(document["checked_at"].utcoffset(), timedelta(0))

    def test_database_failures_return_sanitized_503(self):
        self.collection.insert_one.side_effect = AutoReconnect("private driver details")
        self.collection.find.side_effect = ServerSelectionTimeoutError("private driver details")
        self.collection.aggregate.side_effect = ServerSelectionTimeoutError("private driver details")

        responses = (
            self.http.post("/api/check", json={"url": EXAMPLE_URL}),
            self.http.get("/api/history"),
            self.http.get("/api/analytics"),
        )
        for response in responses:
            with self.subTest(response=response):
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.get_json(), {"error": "Monitoring database unavailable"})

    def test_missing_database_configuration_returns_503(self):
        self.mongo.side_effect = RuntimeError("private configuration details")
        response = self.http.get("/api/history")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"error": "Monitoring database unavailable"})

    def test_history_defaults_to_latest_twenty_and_returns_empty_list(self):
        self.collection.find.return_value = []
        response = self.http.get("/api/history")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), [])
        self.collection.find.assert_called_once_with(
            {}, sort=[("checked_at", -1), ("_id", -1)], limit=20
        )

    def test_history_filters_and_serializes_bson_values(self):
        record_id = ObjectId()
        self.collection.find.return_value = [
            {
                "_id": record_id,
                "url": PYTHON_URL,
                "status": "offline",
                "http_status": None,
                "response_time_ms": None,
                "checked_at": datetime(2026, 11, 20, 12, 30),
                "private": "excluded",
            },
            {
                "_id": ObjectId(),
                "url": PYTHON_URL,
                "status": "online",
                "http_status": 200,
                "response_time_ms": 250.5,
                "checked_at": datetime(2026, 11, 20, 13, tzinfo=timezone(timedelta(hours=1))),
            },
        ]
        response = self.http.get("/api/history", query_string={"url": PYTHON_URL, "limit": 100})

        self.assertEqual(response.status_code, 200)
        self.collection.find.assert_called_once_with(
            {"url": PYTHON_URL}, sort=[("checked_at", -1), ("_id", -1)], limit=100
        )
        first, second = response.get_json()
        self.assertEqual(
            first,
            {
                "id": str(record_id),
                "url": PYTHON_URL,
                "status": "offline",
                "http_status": None,
                "response_time_ms": None,
                "checked_at": "2026-11-20T12:30:00Z",
            },
        )
        self.assertEqual(second["checked_at"], "2026-11-20T12:00:00Z")

    def test_invalid_history_filters_do_not_query_mongodb(self):
        cases = (
            {"url": EXAMPLE_URL + "?query=1"},
            {"url": ""},
            {"limit": "1.5"},
            {"limit": ""},
            {"limit": "-1"},
            {"limit": "0"},
            {"limit": "101"},
        )
        for query in cases:
            with self.subTest(query=query):
                response = self.http.get("/api/history", query_string=query)
                self.assertEqual(response.status_code, 400)
        self.mongo.assert_not_called()

    def test_analytics_calculates_uptime_and_handles_null_timings(self):
        self.collection.aggregate.return_value = [
            {"_id": EXAMPLE_URL, "total": 3, "online": 2, "average_ms": 156.3456},
            {"_id": PYTHON_URL, "total": 2, "online": 0, "average_ms": None},
        ]
        response = self.http.get("/api/analytics")

        self.assertEqual(response.status_code, 200)
        first, second = response.get_json()
        self.assertEqual(
            first,
            {
                "url": EXAMPLE_URL,
                "total_checks": 3,
                "online_checks": 2,
                "offline_checks": 1,
                "uptime_percent": 66.67,
                "average_response_time_ms": 156.35,
            },
        )
        self.assertEqual(second["uptime_percent"], 0)
        self.assertEqual(second["offline_checks"], 2)
        self.assertIsNone(second["average_response_time_ms"])
        pipeline = self.collection.aggregate.call_args.args[0]
        self.assertEqual(pipeline[0]["$group"]["average_ms"], {"$avg": "$response_time_ms"})

    def test_analytics_includes_registered_website_with_no_samples(self):
        self.collection.aggregate.return_value = []
        self.targets.find.return_value = [{"url": PYTHON_URL}]
        response = self.http.get("/api/analytics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([site["url"] for site in response.get_json()], [PYTHON_URL])
        for site in response.get_json():
            self.assertEqual(site["total_checks"], 0)
            self.assertEqual(site["online_checks"], 0)
            self.assertEqual(site["offline_checks"], 0)
            self.assertIsNone(site["uptime_percent"])
            self.assertIsNone(site["average_response_time_ms"])

    def test_analytics_empty_without_history_or_enabled_targets(self):
        self.collection.aggregate.return_value = []
        self.assertEqual(self.http.get("/api/analytics").get_json(), [])

    def test_analytics_can_include_latest_observation_without_changing_default_shape(self):
        self.collection.aggregate.return_value = [{
            "_id": EXAMPLE_URL, "total": 1, "online": 1, "average_ms": 42,
            "latest": {"status": "online", "checked_at": datetime(2026, 11, 20, 12, 30)},
        }]
        default = self.http.get("/api/analytics").get_json()[0]
        self.assertNotIn("latest_check", default)
        response = self.http.get("/api/analytics?include_latest=1")
        self.assertEqual(response.get_json()[0]["latest_check"], {
            "status": "online", "checked_at": "2026-11-20T12:30:00Z",
        })
        group = self.collection.aggregate.call_args.args[0][0]["$group"]
        self.assertEqual(group["latest"]["$top"]["sortBy"], {"checked_at": -1, "_id": -1})
