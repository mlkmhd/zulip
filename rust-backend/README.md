# Zulip Gold backend migration

See `MIGRATION_HANDOFF.md` in this directory for
the verified baseline, Multipass results, and remaining work.

The first Rust component chooses display colors for new channel subscriptions.
The Rust library builds without Python. While Django still handles
subscriptions, it can call the Rust implementation through a Python extension.
The Python implementation stays as the default and as the reference for
comparison. The Rust implementation accepts signed 64-bit recipient IDs,
matching the database ID type.

## Run the compatibility checks

Run from the repository root with Rust 1.89 or newer and a virtual environment
with CPython 3.10 or newer activated:

```sh
cargo test --manifest-path rust-backend/Cargo.toml --locked
python -m pip install ./rust-backend
ZULIP_RUST_STREAM_COLOR=1 python -m unittest discover -s rust-backend/tests
```

The comparison suite checks that the two implementations return the same
colors in the same order and that Rust leaves the input unchanged. It can run
without Django or the database. GitHub Actions runs these checks when the
Rust code or its Python reference changes.

To exercise the existing subscription tests with Rust, install the extension
in Zulip's own virtual environment in your provisioned development environment:

```sh
.venv/bin/python -m pip install ./rust-backend
ZULIP_RUST_STREAM_COLOR=0 ./tools/test-backend zerver.tests.test_subs
ZULIP_RUST_STREAM_COLOR=1 ./tools/test-backend zerver.tests.test_subs
```

If the Zulip virtual environment does not have pip, use
`uv pip install --python .venv/bin/python ./rust-backend` to install the
extension. Set `ZULIP_RUST_STREAM_COLOR=1` before starting a development server
to try the same path there. If you enable it without installing the extension,
Python reports the import error instead of silently running the old code.
Unset the variable to switch back.

`ZULIP_RUST_ALERT_WORDS=1` selects the Rust implementation for trimming and
deduplicating additions through `POST /api/v1/users/me/alert_words` and its
web app equivalent. Django still handles HTTP validation, transactions,
cache invalidation, and events. The extension uses Python's Unicode lowercase
rules so existing words are matched the same way during the transition.

Run the existing alert-word and event tests in both modes:

```sh
ZULIP_RUST_ALERT_WORDS=0 ./tools/test-backend zerver.tests.test_alert_words zerver.tests.test_events.NormalActionsTest.test_alert_words_events
ZULIP_RUST_ALERT_WORDS=1 ./tools/test-backend zerver.tests.test_alert_words zerver.tests.test_events.NormalActionsTest.test_alert_words_events
```

`ZULIP_RUST_USER_STATUS=1` selects Rust for status response fields and the
rules for missing values and clearing an emoji in the user status API.
Django still handles permissions, emoji lookup, and database writes.

```sh
ZULIP_RUST_USER_STATUS=0 ./tools/test-backend zerver.tests.test_user_status
ZULIP_RUST_USER_STATUS=1 ./tools/test-backend zerver.tests.test_user_status
```

`ZULIP_RUST_BOT_STORAGE=1` selects Rust for computing changes to a bot's
character quota on `PUT /api/v1/bot_storage` and when embedded bots write
data. The database reads and writes still use Django.

```sh
ZULIP_RUST_BOT_STORAGE=0 ./tools/test-backend zerver.tests.test_embedded_bot_system
ZULIP_RUST_BOT_STORAGE=1 ./tools/test-backend zerver.tests.test_embedded_bot_system
```

## Run the mobile compatibility API over HTTP

`zulip-http` serves `GET /compatibility` and `HEAD /compatibility` directly in
Rust. Start it in the provisioned environment from the repository root so it
can read the compiled translation catalogs under `locale/`:

```sh
cargo run --manifest-path rust-backend/Cargo.toml --locked --features http --bin zulip-http -- 127.0.0.1:9997
```

In a second terminal in the same environment, use the development proxy to
route requests on the canonical development host to Rust:

```sh
source .venv/bin/activate
./tools/run-dev --rust-http-port 9997
```

The production configuration continues to route requests through Django.
The existing test cases run against Django by default. With the Rust server
running, run the same cases over HTTP by selecting its loopback port:

```sh
./tools/test-backend zerver.tests.test_compatibility
ZULIP_RUST_COMPATIBILITY_TEST_PORT=9997 ./tools/test-backend zerver.tests.test_compatibility
```

## Run the alert-word read API over HTTP

Set `ZULIP_RUST_DATABASE_URL` to a PostgreSQL connection string for the local
Zulip database and `ZULIP_RUST_EXTERNAL_HOST` to the base hostname without a
port. The database role needs read access to `zulip.zerver_userprofile`,
`zulip.zerver_realm`, and `zulip.zerver_alertword`. Use a local Unix socket
or loopback PostgreSQL connection.

```sh
export ZULIP_RUST_DATABASE_URL='host=/var/run/postgresql dbname=zulip user=zulip'
export ZULIP_RUST_EXTERNAL_HOST=zulipdev.com
cargo run --manifest-path rust-backend/Cargo.toml --locked --features http --bin zulip-http -- 127.0.0.1:9997
```

Send `GET /api/v1/users/me/alert_words` to the loopback listener with
`Host: <organization>.zulipdev.com` and HTTP Basic authentication. The user
name is the account email and the password is its API key. The base hostname
selects the root-domain organization. This direct development route supports
the standard realm subdomain layout.

To share the API quota, set `ZULIP_RUST_REDIS_URL` to Django's Redis database
0 and `ZULIP_RUST_RATE_LIMIT_RULES` to the effective
`RATE_LIMITING_RULES["api_by_user"]` as JSON, for example `[[60,200]]`. Use
`[]` for Django's empty rule list. Per-user PostgreSQL overrides take
precedence. `ZULIP_RUST_REDIS_KEY_PREFIX` defaults to the empty prefix used
outside tests. The loopback endpoint applies the quota to all authenticated
clients when Redis is configured.

To publish activity, set `ZULIP_RUST_AMQP_URL` to the AMQP endpoint and vhost
used by Django, including its credentials. Set
`ZULIP_RUST_USER_ACTIVITY_SHARDS` to Django's shard count if it differs from
`1`. The database role needs `SELECT` and `INSERT` on `zulip.zerver_client`
and `USAGE` on its ID sequence so new client names can be registered.
The PostgreSQL session remains read-only when activity is disabled. The
Redis and RabbitMQ settings are independent and optional for direct tests.

In a provisioned Zulip development environment, the comparison test starts Django
and the Rust binary on loopback and gives them the same test database with
committed fixtures.

```sh
source .venv/bin/activate
cargo build --manifest-path rust-backend/Cargo.toml --locked --features http --bin zulip-http
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_alert_words.AlertWordsRustHTTPTest
```

To include the shared Redis and RabbitMQ test, create a dedicated RabbitMQ
vhost beginning with `zulip-rust-test-` and grant the development broker
user access. The test purges and reads its own activity queues in that vhost.

```sh
sudo rabbitmqctl add_vhost zulip-rust-test-alert-words
sudo rabbitmqctl set_permissions -p zulip-rust-test-alert-words zulip '.*' '.*' '.*'
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_alert_words.AlertWordsRustHTTPTest
```

## Read alert words with a Django session

The direct Rust listener also serves `GET /json/users/me/alert_words` and
`HEAD` when session authentication is enabled. It reads Django's session
table and checks the signature and the current password-bound session hash.
The database role needs `SELECT` on `zulip.django_session` in addition to the
permissions above.

Use a private file containing only the current Django `SECRET_KEY`. Set
`ZULIP_RUST_DJANGO_SECRET_KEY_FILE` to its path and set
`ZULIP_RUST_DJANGO_AUTH_BACKENDS` to a JSON list of enabled Zulip authentication
backends whose sessions Rust should accept. For example, to accept development
login sessions:

```sh
export ZULIP_RUST_DJANGO_SECRET_KEY_FILE=/path/to/private/django-secret-key
export ZULIP_RUST_DJANGO_AUTH_BACKENDS='["zproject.backends.DevAuthBackend"]'
```

`ZULIP_RUST_SESSION_COOKIE_NAME` and `ZULIP_RUST_CSRF_COOKIE_NAME` default to
`sessionid` and `csrftoken`. When testing settings with secure cookie names,
set them to `__Host-sessionid` and `__Host-csrftoken`. The current implementation
uses the standard cookie path, SameSite policy, CSRF cookie age and login URL.
It reads database-backed sessions, including Zulip's cached database engine,
through PostgreSQL. Django manages session writes, key rotation and login.

Django and Rust allow these read methods without a CSRF token. Rust replaces a
malformed CSRF cookie and keeps the response private. For writes and their
CSRF checks, use the regular Django route. Run
`AlertWordsRustHTTPTest.test_alert_words_session_and_csrf_parity` with
`ZULIP_RUST_HTTP_BIN` as above. The test creates its own committed sessions.

## Route alert-word reads through the development proxy

Start the Rust HTTP server with the database and session settings above. Set
`ZULIP_RUST_EXTERNAL_HOST` to Django's base hostname without the port, then start
the proxy with both options:

```sh
./tools/run-dev --rust-http-port 9997 --rust-alert-words
```

The proxy forwards `GET` and `HEAD` on `/api/v1/users/me/alert_words`
and `/json/users/me/alert_words` when the hostname selects the same realm
in Django and Rust. Use a standard realm hostname such as
`zulip.zulipdev.com:9991` with the default development settings.
Custom host mappings such as the default `localhost:9991` mapping to the
`zulip` realm are served by Django when the Rust mapping differs. Django also
serves `OPTIONS`, `POST`, `DELETE`, and other methods on these paths.

To restore Django reads, stop the proxy and restart it without
`--rust-alert-words`. Keep `--rust-http-port` if you still want Rust to
serve the mobile compatibility route. A Rust upstream error uses the proxy's
normal error handling.

The proxy smoke test uses committed fixtures and writes only to a disposable
clone of `zulip_test_template` whose name starts with `zulip_rust_proxy_`.
Point both services and the test at that clone. Use `TEST_EXTERNAL_HOST=testserver`,
`EXTERNAL_HOST=testserver:9981`, and the Rust base host `testserver`.
Configure Rust with `ZULIP_RUST_RATE_LIMIT_RULES='[[60,200]]'` and a Redis key prefix
unique to the clone. The Rust rate header distinguishes the upstream from
Django, where the test settings disable rate limiting. Start the proxy with
`--test --streamlined --rust-http-port 9997 --rust-alert-words` and run:

```sh
DJANGO_SETTINGS_MODULE=zproject.test_settings ZULIP_RUST_PROXY_EXPECT_RUST=1 \
    ./manage.py shell --no-imports -c 'from runpy import run_path; run_path("rust-backend/tests/check_alert_words_proxy.py", run_name="__main__")'
```

Restart the proxy without `--rust-alert-words` and repeat with
`ZULIP_RUST_PROXY_EXPECT_RUST=0`, leaving the Rust service running.

## Migration sequence

1. Port backend logic that does not depend on services. For each component,
   compare Rust and Python on the same inputs and rerun its existing Zulip
   integration tests against both paths.
2. Move whole API operations to Rust and replay the same HTTP requests against
   both servers using equivalent database snapshots. Compare responses,
   database writes, and emitted events. Keep API paths and behavior stable so
   existing clients and tests continue to work while routes move.
3. Move long polling, background workers, and integrations. Verify event
   ordering, retries, and cross-organization isolation before switching traffic.
4. Replace the Python deployment tooling, management commands, and test runner.
   Preserve both fresh installs and upgrades from supported releases: verify the
   Rust migration runner against the schemas and data produced by Django's
   historical migrations before retiring them. Remove each Python reference
   only after the equivalent Rust path is in place and its expected results
   are recorded in tests that run without Python.
