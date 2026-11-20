import unittest
from unittest.mock import patch

from pymongo.errors import InvalidURI, ServerSelectionTimeoutError

from app import database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(
            "os.environ",
            {"MONGODB_URI": "mongodb+srv://user:placeholder@cluster.example"},
            clear=True,
        )
        env.start()
        self.addCleanup(env.stop)

        mongo_client = patch("app.database.MongoClient")
        self.mongo_client = mongo_client.start()
        self.addCleanup(mongo_client.stop)

    def test_ping_uses_configured_uri_and_short_timeout(self):
        self.assertTrue(database.ping_database())

        self.mongo_client.assert_called_once_with(
            "mongodb+srv://user:placeholder@cluster.example",
            connect=False,
            serverSelectionTimeoutMS=3000,
        )
        client = self.mongo_client.return_value
        client.__enter__.return_value.admin.command.assert_called_once_with("ping")
        client.__exit__.assert_called_once()

    def test_selects_default_and_configured_database(self):
        client = database.get_mongo_client()
        database.get_database(client)
        client.__getitem__.assert_called_once_with("uptimewatch")

        with patch.dict("os.environ", {"MONGODB_DB_NAME": "uptimewatch_dev"}):
            database.get_database(client)
        client.__getitem__.assert_called_with("uptimewatch_dev")

    def test_ping_returns_false_on_timeout(self):
        client = self.mongo_client.return_value
        command = client.__enter__.return_value.admin.command
        command.side_effect = ServerSelectionTimeoutError()

        self.assertFalse(database.ping_database())
        client.__exit__.assert_called_once()

    def test_missing_uri_skips_client_creation(self):
        with patch.dict("os.environ", {"MONGODB_URI": ""}):
            self.assertFalse(database.ping_database())
        self.mongo_client.assert_not_called()

    def test_invalid_uri_error_hides_driver_details(self):
        self.mongo_client.side_effect = InvalidURI("private connection details")

        with self.assertRaises(RuntimeError) as error:
            database.get_mongo_client()
        self.assertEqual(str(error.exception), "MongoDB client could not be configured.")
        self.assertTrue(error.exception.__suppress_context__)
        self.assertFalse(database.ping_database())
