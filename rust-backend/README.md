# Zulip Gold backend migration

See the [migration handoff and progress report](MIGRATION_HANDOFF.md) for
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
