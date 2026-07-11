"""Run the Telegram bot and payment webhook as sibling container processes."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from threading import Event

from config import settings, validate_settings
from database.init_data import initialize_database


logger = logging.getLogger(__name__)
shutdown_requested = Event()


def build_commands(port: int) -> list[list[str]]:
    """Return the long-running service commands for this container."""
    return [
        [sys.executable, "bot.py"],
        [
            sys.executable,
            "-m",
            "gunicorn",
            "--bind",
            f"0.0.0.0:{port}",
            "--workers",
            "2",
            "--access-logfile",
            "-",
            "--error-logfile",
            "-",
            "webhook_server:app",
        ],
    ]


def build_child_environment() -> dict[str, str]:
    """Mark the schema as prepared while preserving deployment variables."""
    environment = os.environ.copy()
    environment["DATABASE_INITIALIZED"] = "1"
    return environment


def validate_deployment_settings(configured_settings) -> None:
    """Require durable storage and a public callback origin in Docker."""
    if not configured_settings.DATABASE_URL.startswith("postgresql"):
        raise ValueError("Docker deployment requires a PostgreSQL DATABASE_URL")
    if not configured_settings.WEBHOOK_BASE_URL:
        raise ValueError("WEBHOOK_BASE_URL is required for Docker deployment")


def stop_processes(processes, timeout: int = 10) -> None:
    """Gracefully stop all running children, then force any stragglers."""
    running = [process for process in processes if process.poll() is None]
    for process in running:
        process.terminate()

    for process in running:
        try:
            process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, TimeoutError):
            process.kill()
            process.wait()


def supervise(processes) -> int:
    """Wait for shutdown or a child exit and keep service lifecycle atomic."""
    while not shutdown_requested.is_set():
        for process in processes:
            return_code = process.poll()
            if return_code is not None:
                logger.error("Service process %s exited with status %s", process.pid, return_code)
                stop_processes(processes)
                return return_code or 1
        time.sleep(0.5)

    stop_processes(processes)
    return 0


def _request_shutdown(signum, _frame) -> None:
    logger.info("Received signal %s; stopping services", signum)
    shutdown_requested.set()


def main() -> int:
    """Initialize shared state, launch both services, and supervise them."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    shutdown_requested.clear()
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    validate_settings()
    validate_deployment_settings(settings)
    initialize_database()

    processes = []
    try:
        child_environment = build_child_environment()
        for command in build_commands(settings.PORT):
            logger.info("Starting service: %s", " ".join(command))
            processes.append(subprocess.Popen(command, env=child_environment))
        return supervise(processes)
    except Exception:
        logger.exception("Container service startup failed")
        stop_processes(processes)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
