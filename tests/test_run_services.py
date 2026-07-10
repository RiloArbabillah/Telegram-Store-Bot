import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from deployment.run_services import (
    build_child_environment,
    build_commands,
    stop_processes,
    validate_deployment_settings,
)


class RunServicesTests(unittest.TestCase):
    def test_deployment_requires_postgresql(self):
        configured = SimpleNamespace(
            DATABASE_URL="sqlite:///bot_database.db",
            WEBHOOK_BASE_URL="https://bot.example.com",
            PORT=5000,
        )

        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            validate_deployment_settings(configured)

    def test_deployment_requires_webhook_base_url(self):
        configured = SimpleNamespace(
            DATABASE_URL="postgresql+psycopg://user:pass@postgres/store",
            WEBHOOK_BASE_URL="",
            PORT=5000,
        )

        with self.assertRaisesRegex(ValueError, "WEBHOOK_BASE_URL"):
            validate_deployment_settings(configured)

    def test_deployment_accepts_postgresql_and_https_domain(self):
        configured = SimpleNamespace(
            DATABASE_URL="postgresql+psycopg://user:pass@postgres/store",
            WEBHOOK_BASE_URL="https://bot.example.com",
            PORT=5000,
        )

        validate_deployment_settings(configured)

    def test_child_environment_marks_database_as_initialized(self):
        environment = build_child_environment()

        self.assertEqual(environment["DATABASE_INITIALIZED"], "1")

    def test_build_commands_uses_configured_port(self):
        self.assertEqual(
            build_commands(8080),
            [
                [sys.executable, "bot.py"],
                [
                    sys.executable,
                    "-m",
                    "gunicorn",
                    "--bind",
                    "0.0.0.0:8080",
                    "--workers",
                    "2",
                    "--access-logfile",
                    "-",
                    "--error-logfile",
                    "-",
                    "webhook_server:app",
                ],
            ],
        )

    def test_stop_processes_terminates_and_waits_for_running_children(self):
        stopped = Mock()
        stopped.poll.return_value = 0
        running = Mock()
        running.poll.return_value = None

        stop_processes([stopped, running], timeout=3)

        stopped.terminate.assert_not_called()
        running.terminate.assert_called_once_with()
        running.wait.assert_called_once_with(timeout=3)
        running.kill.assert_not_called()

    def test_stop_processes_kills_child_that_misses_grace_period(self):
        running = Mock()
        running.poll.return_value = None
        running.wait.side_effect = [TimeoutError, None]

        stop_processes([running], timeout=1)

        running.terminate.assert_called_once_with()
        running.kill.assert_called_once_with()
        self.assertEqual(running.wait.call_count, 2)


if __name__ == "__main__":
    unittest.main()
