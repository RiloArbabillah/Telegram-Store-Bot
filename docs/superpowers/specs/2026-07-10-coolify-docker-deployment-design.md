# Coolify Docker Deployment Design

## Goal

Deploy the Telegram store bot to one Coolify Dockerfile resource. The container must run both the Telegram polling bot and the public payment webhook server, use PostgreSQL for shared state, and accept all deployment-specific values through environment variables.

## Architecture

The image will contain one Python application and one lightweight Python process launcher. On container startup, the launcher initializes the database schema once and then starts two child processes:

1. `bot.py` runs the Telegram bot with long polling and its scheduled jobs.
2. Gunicorn serves `webhook_server:app` on `0.0.0.0:$PORT`.

The launcher owns both processes, forwards termination signals, and exits if either child exits. It terminates the remaining child before exiting so Coolify can restart the complete application rather than leaving it partially available.

The webhook server remains the only HTTP-facing process. Coolify routes the configured HTTPS domain to the container port. Telegram bot updates continue to use polling; `WEBHOOK_BASE_URL` does not convert the Telegram bot to webhook delivery.

## Configuration Contract

The deployment will use these environment variables:

- `BOT_TOKEN`: required Telegram bot token.
- `ADMIN_TELEGRAM_ID`: required numeric Telegram administrator ID.
- `ADMIN_TELEGRAM_USERNAME`: optional administrator username.
- `DATABASE_URL`: required in production, using SQLAlchemy's psycopg driver, for example `postgresql+psycopg://user:password@postgres:5432/tele_store_bot`.
- `WEBHOOK_BASE_URL`: required public HTTPS origin without a trailing slash, for example `https://bot.example.com`.
- `PORT`: optional HTTP port, defaulting to `5000`.
- Existing payment, mailbox, and DANA environment variables remain supported.

`WEBHOOK_BASE_URL` is configuration for callback endpoint discovery only. Startup will not register or mutate provider webhook settings. The resulting callback URLs are:

- `$WEBHOOK_BASE_URL/webhook/cryptobot`
- `$WEBHOOK_BASE_URL/webhook/dana`
- `$WEBHOOK_BASE_URL/webhook/payment-deka`

When DANA mode is enabled, `DANA_CALLBACK_URL` remains the provider-specific callback value used in DANA API requests. It may be set to `$WEBHOOK_BASE_URL/webhook/dana` in Coolify. Environment variable interpolation is a Coolify concern; the application will consume the final value.

## Container Build

The Dockerfile will:

- Use a pinned slim Python base image.
- Install dependencies from `requirements.txt`, including Gunicorn.
- Copy application source into the image.
- Create a non-root application user.
- Expose port `5000` as documentation while still honoring `$PORT` at runtime.
- Start the process launcher as the container command.

The `.dockerignore` will exclude Git metadata, virtual environments, Python caches, tests' transient output, `.env`, local SQLite files, logs, and local uploaded content that must not be baked into an image.

## Database Startup

The launcher will validate application settings and initialize SQLAlchemy metadata before either long-running process starts. PostgreSQL connectivity failures cause startup to fail with a non-zero exit so Coolify can retry according to its restart policy.

Schema creation remains idempotent through the project's existing `Base.metadata.create_all` path. This deployment targets a fresh PostgreSQL database; converting or importing an existing SQLite database is outside this change.

Both bot and webhook processes use the same `DATABASE_URL`. SQLAlchemy creates separate connection pools per process, which is appropriate because PostgreSQL safely supports concurrent access from both services.

## HTTP and Health Behavior

Gunicorn will bind to `0.0.0.0:$PORT`. The existing `GET /health` endpoint is the Coolify health check and must return HTTP 200 without requiring provider credentials or performing external network calls.

The webhook endpoints remain:

- `POST /webhook/cryptobot`
- `POST /webhook/dana`
- `POST /webhook/payment-deka`

The root route will use `WEBHOOK_BASE_URL` when displaying callback examples, avoiding hard-coded placeholder domains.

## Persistence

Transactional and catalog data live in PostgreSQL and survive image redeploys independently of the application container.

Local runtime media under `uploads/` or `assets/` is not copied from the development workspace into the image. If the deployed bot stores required media on the local filesystem, Coolify should mount persistent storage for those paths. Telegram `file_id` values and remote download links stored in PostgreSQL do not require local persistence.

## Failure Handling

- Invalid required settings fail startup before child processes launch.
- An unavailable PostgreSQL database fails startup rather than starting a webhook with unusable storage.
- If the bot or Gunicorn exits, the launcher terminates the sibling and exits non-zero when appropriate.
- SIGTERM from Coolify is forwarded to both children with a bounded graceful shutdown, followed by forced termination only when needed.
- Gunicorn logs go to stdout/stderr for Coolify log collection.

## Documentation

The README will include a Coolify section covering:

- Creating or attaching a PostgreSQL resource.
- Setting `DATABASE_URL`, `WEBHOOK_BASE_URL`, `PORT`, and existing secrets.
- Configuring the public domain and container port.
- Setting `/health` as the health check.
- Registering the callback endpoints in payment provider dashboards.
- Adding persistent storage only when local media persistence is required.

The `.env.example` will document the new variables without containing real credentials.

## Verification

Implementation is complete when:

1. Existing automated tests pass.
2. New tests verify URL normalization and callback URL construction from `WEBHOOK_BASE_URL`.
3. The Docker image builds successfully.
4. Starting the image with a reachable PostgreSQL database launches both the bot and Gunicorn.
5. `GET /health` returns HTTP 200 through the exposed container port.
6. Stopping the container shuts down both child processes.
7. No `.env`, SQLite database, virtual environment, or unrelated local upload is included in the Docker build context.

## Non-Goals

- Migrating existing SQLite data to PostgreSQL.
- Automatically registering callbacks with CryptoBot, DANA, or payment.deka.dev.
- Changing Telegram update delivery from polling to webhooks.
- Splitting bot and webhook into separate Coolify resources.
