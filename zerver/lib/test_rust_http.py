import os
import re
import select
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection
from psycopg2.extensions import make_dsn


@contextmanager
def rust_http_service(
    extra_env: dict[str, str], *, database_application_name: str | None = None
) -> Iterator[int]:
    db = connection.settings_dict
    database = make_dsn(
        dbname=db["NAME"],
        host=db["HOST"],
        user=db["USER"],
        password=db["PASSWORD"],
        port=db["PORT"] or None,
        application_name=database_application_name,
    )
    external_host = urlsplit("//" + settings.EXTERNAL_HOST).hostname
    assert external_host is not None
    env = os.environ.copy()
    env.pop("ZULIP_RUST_REDIS_URL", None)
    env.pop("ZULIP_RUST_AMQP_URL", None)
    env.pop("ZULIP_RUST_DJANGO_SECRET_KEY_FILE", None)
    env.pop("ZULIP_RUST_CLIENT_IP_SOURCE", None)
    env.pop("ZULIP_RUST_DEBUG_RATE_LIMITING", None)
    env.pop("ZULIP_RUST_SAVED_SNIPPET_WRITES", None)
    env.pop("ZULIP_RUST_WEB_POST_SCHEME", None)
    with subprocess.Popen(
        [os.environ["ZULIP_RUST_HTTP_BIN"], "127.0.0.1:0"],
        stderr=subprocess.PIPE,
        text=True,
        env={
            **env,
            "ZULIP_RUST_DATABASE_URL": database,
            "ZULIP_RUST_EXTERNAL_HOST": external_host,
            **extra_env,
        },
    ) as rust:
        assert rust.stderr is not None
        try:
            ready, _, _ = select.select([rust.stderr], [], [], 10)
            assert ready, "Rust HTTP service did not start"
            address = re.fullmatch(
                r"Zulip HTTP listening on 127\.0\.0\.1:(\d+)\n", rust.stderr.readline()
            )
            assert address is not None, "Rust HTTP service failed to bind"
            yield int(address[1])
        finally:
            rust.terminate()
            try:
                rust.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rust.kill()


def request_test_http(
    port: int,
    method: str,
    host: str,
    headers: dict[str, str],
    path: str,
    *,
    body: bytes | str | None = None,
) -> tuple[int, bytes, dict[str, str | None], str | None]:
    client = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        client.request(
            method,
            path,
            body=body,
            headers={
                "Host": host,
                "User-Agent": "ZulipMobile/1.0",
                "X-Real-IP": "192.0.2.1",
                **headers,
            },
        )
        response = client.getresponse()
        compared_headers = (
            "Content-Type",
            "Content-Length",
            "Content-Language",
            "Vary",
            "Cache-Control",
            "WWW-Authenticate",
            "Location",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
            "Set-Cookie",
        )
        return (
            response.status,
            response.read(),
            {name: response.getheader(name) for name in compared_headers},
            response.getheader("Expires"),
        )
    finally:
        client.close()
