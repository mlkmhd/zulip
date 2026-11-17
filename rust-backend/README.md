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
web app equivalent. It also executes the owner-scoped PostgreSQL delete
for `DELETE /api/v1/users/me/alert_words` and the matching web route.
Rust uses the cursor from Django's transaction. Django handles HTTP validation,
input lowercasing for deletion, cache invalidation, and post-commit events.
The addition bridge uses Python's Unicode lowercase rules for deduplication.

Reinstall the extension before enabling the flag to load the current
deletion function.

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
outside tests. Basic Auth requests from the `internal` client are exempt
when the request originates at `127.0.0.1` or `::1`, or when
`ZULIP_RUST_DEBUG_RATE_LIMITING=1` mirrors Django's `DEBUG_RATE_LIMITING=True`.
Client names are case-insensitive for this check. Exempt requests still
authenticate and record activity when RabbitMQ is configured.

Set `ZULIP_RUST_CLIENT_IP_SOURCE=proxy` when the loopback listener is behind
a trusted proxy that overwrites `X-Real-IP` with the verified client address.
The supplied nginx include and the Rust read routes in `tools/run-dev` do
this. For a listener used directly, set `ZULIP_RUST_CLIENT_IP_SOURCE=peer`
to use the TCP peer address. With the setting unset, the server applies
the quota unless debug mode exempts the client.

To publish activity, set `ZULIP_RUST_AMQP_URL` to the AMQP endpoint and vhost
used by Django, including its credentials. Set
`ZULIP_RUST_USER_ACTIVITY_SHARDS` to Django's shard count if it differs from
`1`. The database role needs `SELECT` and `INSERT` on `zulip.zerver_client`
and `USAGE` on its ID sequence so new client names can be registered.
The PostgreSQL session remains read-only when activity is disabled. The
Redis and RabbitMQ settings are independent and optional for direct tests.
If PostgreSQL disconnects after startup, the next database-backed request
or readiness probe can reopen the connection. Requests that fail after
acquiring a connection return an error without retrying the operation.

In a provisioned Zulip development environment, the comparison test starts Django
and the Rust binary on loopback and gives them the same test database with
committed fixtures.

```sh
source .venv/bin/activate
cargo build --manifest-path rust-backend/Cargo.toml --locked --features http --bin zulip-http
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_alert_words.AlertWordsRustHTTPTest
```

This suite also terminates the Rust connection in the test database and
checks that the same Rust process serves the original response after reconnecting.

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

## Read saved snippets through Rust

Start `zulip-http` with the PostgreSQL and host settings used for the
alert-word API. The database role also needs `SELECT` on
`zulip.zerver_savedsnippet`. `GET /api/v1/saved_snippets` and its `HEAD`
variant use the same Basic authentication, Redis quota and RabbitMQ activity
configuration as alert-word reads. The activity query is `get_saved_snippets`.

To use this API through the development proxy, start it with:

```sh
./tools/run-dev --rust-http-port 9997 --rust-saved-snippets
```

The proxy sends `GET` and `HEAD` to Rust for hostnames that select the same
realm in Django and Rust. The web route and write methods have separate
switches. Restart the proxy without `--rust-saved-snippets` to return API
reads to Django.

With the same test binary and database setup as above, run the HTTP comparison:

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ./tools/test-backend zerver.tests.test_saved_snippets
```

Omit `ZULIP_RUST_HTTP_BIN` to run the same cases on Django alone.

## Stage saved-snippet reads in nginx

Start `zulip-http` on `127.0.0.1:9997` with the saved-snippet read settings,
`ZULIP_RUST_CLIENT_IP_SOURCE=proxy`, and the staging Redis and RabbitMQ
settings. The nginx `proxy` include supplies `X-Real-IP` from
`$trusted_remote_addr`, the same address sent to Django. For a staging
instance that accepts requests directly, set `$trusted_remote_addr` from
`$remote_addr`.

1. Include `saved-snippets-map.conf` from `rust-backend/staging/nginx/` in
   nginx's HTTP context. The sample verifies `zulip.testserver` with or
   without `:8880`. Substitute the hostname and port that select the same
   organization in Rust and Django.
2. Verify an authenticated `GET /api/v1/saved_snippets` directly on the
   Rust listener using the staging hostname. Place `saved-snippets-routes.conf`
   in the staging `zulip-include/app.d/` directory, run `nginx -t`, and reload.
3. Check `GET` and `HEAD` through nginx. Use `OPTIONS` and a test write on a
   disposable database to check the Django route. Compare an authenticated
   ordinary request, a loopback `internal` request, and a nonlocal `internal`
   request against the same quota. The nonlocal request remains subject to
   the quota even when it sends its own loopback `X-Real-IP`.

To roll back, remove `saved-snippets-routes.conf` from `app.d/`, run `nginx -t`,
and reload. While the Rust route is active, an unreachable Rust listener
produces the existing nginx JSON error response. nginx keeps the request on
the selected upstream to avoid duplicating quota and activity side effects.

### Enable saved-snippet creation

Set `ZULIP_RUST_SAVED_SNIPPET_WRITES=1` on `zulip-http` with the staging
`ZULIP_RUST_AMQP_URL`. This slice targets a single Tornado process that
consumes `notify_tornado`. Use `ZULIP_RUST_MAX_MESSAGE_LENGTH` if Django's
`MAX_MESSAGE_LENGTH` differs from `10000`. The PostgreSQL role also needs
`INSERT` on `zulip.zerver_savedsnippet` and `zulip.zerver_realmauditlog`,
`SELECT` on the returned columns, and `USAGE` on both ID sequences.

To route `POST /api/v1/saved_snippets` on the verified staging host, replace
the read-only HTTP map with `saved-snippets-write-map.conf`, keep
`saved-snippets-routes.conf` installed, run `nginx -t`, and reload. In the
development proxy, use `--rust-http-port 9997 --rust-saved-snippet-writes`.
The `--rust-saved-snippets` option controls reads independently.

Rust commits the snippet and audit row together, then publishes the
`saved_snippets/add` event for the owner to RabbitMQ. If RabbitMQ cannot
confirm the publication after commit, the endpoint returns `503` and logs
the saved snippet ID. Check whether the snippet exists before resubmitting
an ambiguous result.

To roll back writes while keeping Rust reads, reinstall the read-only map
and reload nginx, or remove `--rust-saved-snippet-writes` from the development
proxy. The item routes have separate routing from the collection route.

Use the dedicated RabbitMQ vhost described in the alert-word instructions
to compare PostgreSQL rows and events from both implementations:

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest
```

### Enable saved-snippet editing

The same `ZULIP_RUST_SAVED_SNIPPET_WRITES=1` setting enables `PATCH` on the
Rust listener. Grant the PostgreSQL role `UPDATE` on the `title` and
`content` columns of `zulip.zerver_savedsnippet`. A request can set either
field or both. Sending neither succeeds without changing the snippet or
sending a `saved_snippets/update` event. A committed update
publishes the full snippet to the owner's Tornado queue.

For nginx staging, include `saved-snippet-edit-map.conf` in the HTTP
context and add `saved-snippet-edit-routes.conf` to `zulip-include/app.d/`.
Use the verified realm hostname and port in the map, run `nginx -t`, and
reload. This map selects `PATCH /api/v1/saved_snippets/{id}`. Remove the
detail routes include and reload to return item requests to Django
independently of GET and POST on the collection.

For the development proxy, use `--rust-http-port 9997 --rust-saved-snippet-edits`.
The comparison command above also checks that patches preserve omitted fields,
deny edits to another user's snippet, and publish the same event as Django.

### Enable saved-snippet deletion

`ZULIP_RUST_SAVED_SNIPPET_WRITES=1` enables `DELETE` on the Rust listener.
Grant the PostgreSQL role `DELETE` on `zulip.zerver_savedsnippet`. Only the
authenticated owner's row is eligible for deletion. After PostgreSQL
commits, Rust publishes `saved_snippets/remove` with the deleted ID to the
owner's Tornado queue. A repeated deletion returns `404`. An existing
creation audit record is retained.

For nginx staging, replace `saved-snippet-edit-map.conf` with
`saved-snippet-delete-map.conf`, retaining the detail routes include. The
new map selects both PATCH and DELETE for the verified host. Run `nginx -t`
and reload. To roll back only DELETE, restore the edit map and reload.
For the development proxy, use `--rust-http-port 9997 --rust-saved-snippet-deletes`.

The HTTP comparison above also checks the deleted row, event payload,
and response for repeated or competing delete requests.

### Enable saved-snippet web reads

Configure the Django secret, authentication backends, and cookie names as
described in [Read alert words with a Django session](#read-alert-words-with-a-django-session).
The PostgreSQL role needs `SELECT` on `zulip.django_session` and
`zulip.zerver_savedsnippet`. Rust reads the signed session from PostgreSQL
and returns the authenticated user's snippets. GET and HEAD use the usual
Django behavior for safe methods, including replacement of a malformed
CSRF cookie. The read-only map sends writes and `OPTIONS` to Django.

For nginx staging, include `saved-snippets-web-map.conf` in the HTTP
context and add `saved-snippets-web-routes.conf` to `zulip-include/app.d/`.
Use the verified realm hostname and port in the map, run `nginx -t`, and
reload. The map selects `GET` and `HEAD` on `/json/saved_snippets`. Remove
the web routes include and reload to return reads to Django independently
of the API routes.

For the development proxy, pass `--rust-http-port 9997 --rust-saved-snippets-web`.
The HTTP comparison in `zerver.tests.test_saved_snippets` also checks
session authentication, requester isolation, and CSRF cookie behavior.

### Enable saved-snippet web creation

Configure [Django sessions](#read-alert-words-with-a-django-session) and the
[write settings and database grants](#enable-saved-snippet-creation) for
`zulip-http`. Set `ZULIP_RUST_WEB_POST_SCHEME` to the scheme browsers use
to reach this listener through the proxy. Use `https` for a TLS-terminating
proxy and `http` for local HTTP development. Use one public scheme per
listener and redirect HTTP to HTTPS at the proxy for a TLS deployment.

This route targets Zulip's cookie-based CSRF settings, the default
`X-CSRFToken` header, and same-origin requests. Keep POST on Django for
deployments that use cross-origin CSRF trust, a custom CSRF header,
session-backed CSRF, or a shared CSRF cookie domain. Rust checks the Django
session before comparing the CSRF cookie with the form or header token.
It requires a matching Origin when present and a same-host HTTPS Referer
when Origin is absent on HTTPS. The configured scheme controls this check.

Replace `saved-snippets-web-map.conf` with
`saved-snippets-web-write-map.conf` in the nginx HTTP context. Keep the
same verified host and port and the same `saved-snippets-web-routes.conf`
include. Run `nginx -t` and reload. This map selects the collection route.
To return only POST to Django, restore the read-only map and
reload. nginx returns its JSON upstream error if Rust is unavailable.

With the development proxy, add `--rust-saved-snippets-web-writes` to
`--rust-http-port 9997`. Use `--rust-saved-snippets-web` for web reads.
Remove the write flag to return POST to Django.

Compare session and CSRF handling, the committed rows, and the
owner-only notification with the dedicated RabbitMQ test vhost.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest.test_saved_snippet_web_post_http_and_event_parity
```

### Enable saved-snippet web editing

Use the [web creation settings](#enable-saved-snippet-web-creation),
including `ZULIP_RUST_WEB_POST_SCHEME`, and grant the Rust database role
`UPDATE` on `zulip.zerver_savedsnippet`. For
`PATCH /json/saved_snippets/{id}`, Rust checks the Django session and
compares the CSRF cookie with the `X-CSRFToken` header before applying
the quota, recording activity, or updating the snippet. Django's PATCH
contract uses the header token. A committed edit publishes the full
snippet to the owner through Tornado.

In nginx staging, include `saved-snippet-web-edit-map.conf` in the HTTP
context and install `saved-snippet-web-edit-routes.conf` in
`zulip-include/app.d/`. Use the verified hostname and port, run
`nginx -t`, and reload. The edit map selects PATCH on the numeric detail
path. Django serves OPTIONS and POST method overrides on that path. An
unavailable Rust listener returns nginx's JSON upstream error. To return
all web detail methods to Django, remove the detail routes include and
reload.

In the development proxy, use
`--rust-http-port 9997 --rust-saved-snippets-web-edits`. Remove the edit
flag to return web PATCH to Django.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest.test_saved_snippet_web_patch_http_and_event_parity
```

### Enable saved-snippet web deletion

Use the [web creation settings](#enable-saved-snippet-web-creation) and
grant the Rust database role `DELETE` on `zulip.zerver_savedsnippet`.
`DELETE /json/saved_snippets/{id}` requires a Django session and the
`X-CSRFToken` header. After removing the owner's row, Rust publishes the
deleted ID to the owner's Tornado queue. The creation audit entry is
retained. A repeated deletion returns `404`.

For nginx staging, replace `saved-snippet-web-edit-map.conf` with
`saved-snippet-web-delete-map.conf` and retain the detail routes include.
The new map selects PATCH and DELETE for the verified host and port. Run
`nginx -t` and reload. Restore the edit map and reload to return DELETE
to Django while keeping PATCH on Rust.

In the development proxy, use
`--rust-http-port 9997 --rust-saved-snippets-web-deletes`. Remove the
delete flag to return DELETE to Django.

```sh
ZULIP_RUST_HTTP_BIN="$PWD/rust-backend/target/debug/zulip-http" \
    ZULIP_RUST_ACTIVITY_TEST_VHOST=zulip-rust-test-alert-words \
    ./tools/test-backend zerver.tests.test_saved_snippets.SavedSnippetWriteHTTPTest.test_saved_snippet_web_delete_http_and_event_parity
```

## Stage alert-word reads in nginx

Start `zulip-http` on `127.0.0.1:9997` with PostgreSQL, session settings, and
the staging Redis and RabbitMQ settings. Use the snippets under
`rust-backend/staging/nginx/` in the staging instance:

1. Include `alert-words-map.conf` in nginx's HTTP context. The sample allows
   `zulip.testserver` with or without port `8880`. Replace this with the exact
   staging host and port after confirming that Django and Rust resolve it to
   the same organization.
2. Place `alert-words-health.conf` in the staging `zulip-include/app.d/`
   directory. Validate and reload nginx, then probe
   `GET /health/rust-alert-words` from loopback. Expect `200` with
   `{"ready":true}` before enabling the routes. The probe checks session
   configuration, reads the relevant PostgreSQL tables, pings Redis when
   configured, and checks the RabbitMQ connection and channel when configured.
   `503` indicates a running but unready service. Django's `/health` remains
   available separately.
3. Place `alert-words-routes.conf` in the same `app.d/` directory. Run
   `nginx -t` and reload the staging instance. The two alert-word routes
   send `GET` and `HEAD` to Rust for the allowed host. Every other method
   follows the existing Django uWSGI route.

To roll back, remove `alert-words-routes.conf` from the staging `app.d/`
directory, validate the configuration, and reload nginx. The readiness probe
can stay installed during rollback. An upstream failure uses the existing
API error response and requires an explicit routing rollback. This avoids
replaying a read that may already have updated quota or activity.

The smoke test above also runs against nginx with
`ZULIP_RUST_PROXY_PORT=8880`. Use `ZULIP_RUST_PROXY_EXPECT_RUST=1` with the
routing include installed, then `0` after removing it and reloading. During
testing, use a disposable Django database and dedicated dependency instances
when exercising outages.

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
