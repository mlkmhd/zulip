# ruff: noqa: INP001
"""Check alert-word proxy routing against a disposable Zulip test database."""

import os
from base64 import b64encode
from http.client import HTTPConnection
from urllib.parse import urlencode
from uuid import uuid4

import orjson
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.middleware.csrf import _get_new_csrf_string

from zerver.lib.alert_words import user_alert_words
from zerver.models import AlertWord, UserProfile


def check_proxy(rust_enabled: bool) -> None:
    assert settings.TEST_SUITE and connection.settings_dict["NAME"].startswith("zulip_rust_proxy_")
    proxy_port = int(os.environ.get("ZULIP_RUST_PROXY_PORT", "9981"))
    user = UserProfile.objects.get(delivery_email="hamlet@zulip.com", realm__string_id="zulip")
    original_words = user_alert_words(user)
    word = f"rust-proxy-{uuid4().hex}"
    session = SessionStore()
    session.update(
        {
            "_auth_user_id": str(user.id),
            "_auth_user_backend": "zproject.backends.DevAuthBackend",
            "_auth_user_hash": user.get_session_auth_hash(),
        }
    )
    session.create()
    csrf = _get_new_csrf_string()
    browser = {
        "Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}; {settings.CSRF_COOKIE_NAME}={csrf}",
        "X-CSRFToken": csrf,
    }
    api = {
        "Authorization": "Basic "
        + b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()
    }
    checks = 0

    def request(
        method: str,
        path: str,
        headers: dict[str, str],
        status: int,
        *,
        rust: bool = False,
        host: str = user.realm.host,
        fields: dict[str, str] | None = None,
    ) -> tuple[bytes, str | None]:
        nonlocal checks
        client = HTTPConnection("127.0.0.1", proxy_port, timeout=10)
        try:
            outgoing = {"Host": host, "X-Real-IP": "192.0.2.1", **headers}
            if fields is not None:
                outgoing["Content-Type"] = "application/x-www-form-urlencoded"
            client.request(
                method, path, headers=outgoing, body=urlencode(fields) if fields else None
            )
            response = client.getresponse()
            body = response.read()
            label = (method, path, host)
            assert response.status == status, (label, response.status, status)
            # Django's test settings disable rate headers. The configured
            # Rust-only quota makes the selected upstream visible on successful reads.
            assert response.getheader("X-RateLimit-Limit") == ("200" if rust else None), label
            checks += 1
            return body, response.getheader("Allow")
        finally:
            client.close()

    try:
        for prefix, auth in (("/api/v1", api), ("/json", browser)):
            path = f"{prefix}/users/me/alert_words"
            body, _ = request("GET", path + "?client=ZulipMobile", auth, 200, rust=rust_enabled)
            assert orjson.loads(body)["alert_words"] == original_words
            body, _ = request("HEAD", path, auth, 200, rust=rust_enabled)
            assert body == b""
            _, allow = request("OPTIONS", path, {}, 204)
            assert allow == "DELETE, GET, HEAD, POST"
            body, _ = request("PATCH", path, auth, 405)
            assert set(orjson.loads(body)["allowed_methods"]) == {"GET", "HEAD", "POST", "DELETE"}

            fields = {"alert_words": orjson.dumps([word]).decode()}
            request("POST", path, auth, 200, fields=fields)
            assert word in user_alert_words(user)
            request("POST", path, auth, 200, fields={"method": "GET"})
            assert word in user_alert_words(user)
            request("DELETE", path, auth, 200, fields=fields)
            assert word not in user_alert_words(user)

        path = "/json/users/me/alert_words"
        body, _ = request("POST", path, {"Cookie": browser["Cookie"]}, 403, fields=fields)
        assert orjson.loads(body)["code"] == "CSRF_FAILED"
        assert sorted(user_alert_words(user)) == sorted(original_words)

        path = "/api/v1/users/me/alert_words"
        body, _ = request("GET", path, api, 401, host="outside.testserver.invalid")
        assert orjson.loads(body)["msg"] == "Account is not associated with this subdomain"
        request("GET", path + "/not-the-endpoint", api, 404)
    finally:
        session.delete()
        AlertWord.objects.filter(user_profile=user, word=word).delete()
    print(
        f"{checks} proxy checks passed with Rust alert words {'enabled' if rust_enabled else 'disabled'}."
    )


if __name__ == "__main__":
    check_proxy(rust_enabled={"0": False, "1": True}[os.environ["ZULIP_RUST_PROXY_EXPECT_RUST"]])
