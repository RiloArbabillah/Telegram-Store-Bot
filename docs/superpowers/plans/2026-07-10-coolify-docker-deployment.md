# Coolify Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one production Docker image that runs the Telegram polling bot and payment webhook together on Coolify using an environment-configured PostgreSQL database and webhook domain.

**Architecture:** Add normalized deployment settings and callback URL helpers, then serve Flask with Gunicorn while a small Python supervisor runs Gunicorn and the polling bot as sibling processes. The supervisor validates settings and initializes PostgreSQL before launch, forwards shutdown signals, and fails the container if either service dies.

**Tech Stack:** Python 3.12, python-telegram-bot, Flask, Gunicorn, SQLAlchemy, psycopg 3, Docker, PostgreSQL, unittest

---

## File Structure

- Create `deployment/__init__.py`: marks deployment helpers as a package.
- Create `deployment/run_services.py`: startup validation, schema initialization, child process supervision, and signal handling.
- Create `tests/test_deployment_settings.py`: tests environment normalization and callback URL generation.
- Create `tests/test_run_services.py`: tests child command construction and sibling shutdown behavior.
- Create `Dockerfile`: production image definition and runtime command.
- Create `.dockerignore`: excludes secrets and local/runtime artifacts from the build context.
- Modify `config/settings.py`: loads `WEBHOOK_BASE_URL`, `PORT`, and validates production URL/database settings.
- Modify `webhook_server.py`: displays callback URLs derived from configuration.
- Modify `requirements.txt`: adds pinned Gunicorn runtime dependency.
- Modify `.env.example`: documents PostgreSQL, webhook base URL, and port variables.
- Modify `README.md`: adds exact Coolify deployment and provider callback instructions.

### Task 1: Deployment Settings and Callback URLs

**Files:**
- Modify: `config/settings.py`
- Modify: `webhook_server.py`
- Create: `tests/test_deployment_settings.py`

- [ ] **Step 1: Write failing settings tests**

Add tests that reload `config.settings` with patched environment values and assert:

```python
self.assertEqual(settings.WEBHOOK_BASE_URL, "https://bot.example.com")
self.assertEqual(settings.PORT, 8080)
self.assertEqual(
    settings.callback_url("/webhook/dana"),
    "https://bot.example.com/webhook/dana",
)
```

Also assert an invalid `PORT` falls back to `5000`, and that a trailing slash is removed from `WEBHOOK_BASE_URL`.

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run: `venv/bin/python -m unittest tests.test_deployment_settings`

Expected: FAIL because `WEBHOOK_BASE_URL`, `PORT`, and `callback_url` do not exist.

- [ ] **Step 3: Implement normalized deployment settings**

Add to `Settings`:

```python
WEBHOOK_BASE_URL = _get_env("WEBHOOK_BASE_URL").rstrip("/")
PORT = _get_int_env("PORT", 5000) or 5000

def callback_url(self, path: str) -> str:
    normalized_path = "/" + path.lstrip("/")
    return f"{self.WEBHOOK_BASE_URL}{normalized_path}" if self.WEBHOOK_BASE_URL else normalized_path
```

Extend validation so a configured webhook base URL must start with `https://` and `DATABASE_URL` must be non-empty. Keep SQLite available for local development; production PostgreSQL is selected through `.env`.

- [ ] **Step 4: Render configured callback URLs on the webhook root route**

Replace hard-coded `https://your-domain.com` examples with values returned by:

```python
settings.callback_url("/webhook/cryptobot")
settings.callback_url("/webhook/dana")
settings.callback_url("/webhook/payment-deka")
```

- [ ] **Step 5: Run focused tests**

Run: `venv/bin/python -m unittest tests.test_deployment_settings`

Expected: all deployment settings tests pass.

### Task 2: Process Supervisor

**Files:**
- Create: `deployment/__init__.py`
- Create: `deployment/run_services.py`
- Create: `tests/test_run_services.py`

- [ ] **Step 1: Write failing supervisor tests**

Test pure command construction:

```python
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
```

Test that when one fake child reports an exit code, `stop_processes` terminates and waits for the still-running sibling.

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run: `venv/bin/python -m unittest tests.test_run_services`

Expected: FAIL because `deployment.run_services` does not exist.

- [ ] **Step 3: Implement startup and supervision**

Implement focused functions:

```python
def build_commands(port: int) -> list[list[str]]: ...
def stop_processes(processes, timeout: int = 10) -> None: ...
def supervise(processes) -> int: ...
def main() -> int: ...
```

`main` calls `validate_settings()` and `initialize_database()` before starting children with `subprocess.Popen`. Register SIGTERM and SIGINT handlers that request shutdown. Poll children at a short interval, stop the sibling when one exits, and return the failed child's non-zero status or `1` when a long-running service exits cleanly unexpectedly.

- [ ] **Step 4: Run focused supervisor tests**

Run: `venv/bin/python -m unittest tests.test_run_services`

Expected: all supervisor tests pass without starting Telegram or Gunicorn.

### Task 3: Production Docker Image

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Modify: `requirements.txt`

- [ ] **Step 1: Add the production WSGI dependency**

Append a pinned compatible Gunicorn dependency:

```text
gunicorn==22.0.0
```

- [ ] **Step 2: Add a secret-safe Docker build context**

Create `.dockerignore` excluding at least:

```text
.git
.env
.env.*
!.env.example
venv/
.venv/
__pycache__/
*.py[cod]
*.db
*.sqlite*
uploads/*
!uploads/__init__.py
```

- [ ] **Step 3: Create the non-root Docker image**

Create a multi-layer Dockerfile based on `python:3.12-slim`, install requirements with `pip --no-cache-dir`, copy the source, create writable `uploads` and `assets` directories owned by an `app` user, set `PYTHONUNBUFFERED=1`, expose `5000`, and run:

```dockerfile
CMD ["python", "-m", "deployment.run_services"]
```

- [ ] **Step 4: Verify Dockerfile syntax and image build**

Run: `docker build -t tele-store-bot:coolify .`

Expected: build succeeds and `.env`, local SQLite data, virtualenv, and unrelated uploads are absent from copied layers.

### Task 4: Coolify Configuration Documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Document deployment variables**

Change the example database URL and add:

```dotenv
DATABASE_URL=postgresql+psycopg://tele_store_bot:change_me@postgres:5432/tele_store_bot
WEBHOOK_BASE_URL=https://bot.example.com
PORT=5000
```

Keep all secrets empty or visibly fake.

- [ ] **Step 2: Add a Coolify deployment guide**

Document these exact actions:

1. Create PostgreSQL in Coolify and use its internal host in `DATABASE_URL`.
2. Create a Dockerfile application from this repository.
3. Configure the environment variables and public domain.
4. Route the domain to port `5000` and use `/health` for health checks.
5. Register the three callback URLs manually with the relevant providers.
6. Optionally mount persistent volumes at `/app/uploads` and `/app/assets` for local media.

- [ ] **Step 3: Check documentation consistency**

Run:

```bash
rg -n "DATABASE_URL|WEBHOOK_BASE_URL|PORT|/health|webhook/cryptobot|webhook/dana|webhook/payment-deka" .env.example README.md
```

Expected: every required setting and endpoint appears in both the example/configuration guidance where applicable.

### Task 5: End-to-End Verification

**Files:**
- Test: all files above

- [ ] **Step 1: Run all automated tests**

Run: `venv/bin/python -m unittest discover -s tests`

Expected: all tests pass.

- [ ] **Step 2: Check source and Docker context hygiene**

Run:

```bash
git diff --check
docker run --rm tele-store-bot:coolify sh -c 'test ! -f /app/.env && test ! -f /app/bot_database.db'
```

Expected: both commands exit zero.

- [ ] **Step 3: Verify against PostgreSQL**

Start a temporary PostgreSQL container, run the application image on the same temporary Docker network with a placeholder Telegram token and numeric admin ID, and verify `curl http://localhost:<mapped-port>/health` returns JSON containing `"status":"ok"`. Confirm logs contain database initialization plus startup lines for both services. Stop the application and verify its container exits, then remove the temporary database and network.

- [ ] **Step 4: Review final diff and commit implementation**

Run `git status --short` and `git diff --stat`, confirm the unrelated local logo remains untracked, then stage only implementation files and commit with:

```bash
git commit -m "Add Coolify Docker deployment"
```
