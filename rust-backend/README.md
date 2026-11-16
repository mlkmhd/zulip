# Zulip Gold backend migration

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
