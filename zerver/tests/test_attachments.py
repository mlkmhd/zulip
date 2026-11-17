import os
import re
from base64 import b64encode
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock
from urllib.parse import urlsplit

import orjson
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import LiveServerTestCase
from typing_extensions import override

from analytics.models import RealmCount
from zerver.lib.attachments import user_attachments
from zerver.lib.cache import cache_delete, get_realm_used_upload_space_cache_key
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_helpers import get_test_image_file
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.lib.thumbnail import ThumbnailFormat
from zerver.models import Attachment, ImageAttachment, Message, UserProfile


class AttachmentReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_get_attachments_parity_with_rust(self) -> None:
        user = self.example_user("hamlet")
        other = self.example_user("othello")
        empty = self.example_user("iago")
        external = self.mit_user("sipbtest")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None
        api = "/api/v1/attachments"
        web = "/json/attachments"
        day = datetime(1970, 1, 1, tzinfo=timezone.utc)
        cache_key = get_realm_used_upload_space_cache_key(user.realm_id)

        def api_auth(profile: UserProfile) -> dict[str, str]:
            token = b64encode(f"{profile.delivery_email}:{profile.api_key}".encode()).decode()
            return {"Authorization": f"Basic {token}"}

        with TemporaryDirectory() as directory, ExitStack() as cleanup:
            session = SessionStore()
            session.update(
                {
                    "_auth_user_id": str(user.id),
                    "_auth_user_backend": "zproject.backends.DevAuthBackend",
                    "_auth_user_hash": user.get_session_auth_hash(),
                }
            )
            session.create()
            cleanup.callback(session.delete)
            cleanup.callback(cache_delete, cache_key)

            def create_attachment(
                owner: UserProfile, name: str, size: int, created: datetime
            ) -> Attachment:
                attachment = Attachment.objects.create(
                    owner=owner,
                    realm=owner.realm,
                    file_name=name,
                    path_id=f"{owner.realm_id}/rust-test/{name}",
                    create_time=created,
                    size=size,
                )
                cleanup.callback(Attachment.objects.filter(id=attachment.id).delete)
                return attachment

            old = create_attachment(user, "old.txt", 9, day - timedelta(milliseconds=250))
            linked = create_attachment(user, "یادداشت.txt", 60, day)
            unlinked = create_attachment(user, "unlinked.txt", 30, day + timedelta(seconds=1))
            private = create_attachment(other, "private.txt", 70, day)
            foreign = create_attachment(external, "foreign.txt", 400, day)
            messages = list(
                Message.objects.filter(realm=user.realm, sender=user)
                .order_by("id")
                .values_list("id", flat=True)[:2]
            )
            self.assert_length(messages, 2)
            linked.messages.add(*reversed(messages))
            cleanup.callback(linked.messages.clear)
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            use_rust = os.environ.get("ZULIP_RUST_HTTP_BIN")
            service = (
                rust_http_service(
                    {
                        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                            settings.AUTHENTICATION_BACKENDS
                        ).decode(),
                    }
                )
                if use_rust
                else nullcontext(None)
            )
            with service as rust_port:
                cookie = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}

                def check(
                    method: str, path: str, host: str, headers: dict[str, str], status: int
                ) -> bytes:
                    django = request_test_http(django_port, method, host, headers, path)
                    self.assertEqual(django[0], status, django[1])
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, host, headers, path)
                        self.assertEqual((rust[0], rust[2]), (django[0], django[2]))
                        if method == "GET" and status == 200:
                            rust_data, django_data = orjson.loads(rust[1]), orjson.loads(django[1])
                            for data in (rust_data, django_data):
                                for attachment in data["attachments"]:
                                    attachment["message_ids"].sort()
                                data["attachments"].sort(key=lambda attachment: attachment["id"])
                            self.assertEqual(rust_data, django_data)
                        else:
                            self.assertEqual(rust[1], django[1])
                    return django[1]

                def assert_attachments(response: bytes, used: int, *expected: Attachment) -> None:
                    data = orjson.loads(response)
                    self.assertEqual(data["upload_space_used"], used)
                    expected_values = [attachment.to_dict() for attachment in expected]
                    for attachment in [*data["attachments"], *expected_values]:
                        attachment["message_ids"].sort()
                    self.assertCountEqual(data["attachments"], expected_values)

                self.assertEqual(old.to_dict()["create_time"], -1)
                for path, headers in ((api, api_auth(user)), (web, cookie)):
                    response = check("GET", path + "?extra=ignored", user.realm.host, headers, 200)
                    assert_attachments(response, 169, old, linked, unlinked)
                    self.assertEqual(check("HEAD", path, user.realm.host, headers, 200), b"")
                    check("GET", path, user.realm.host, {}, 401)

                assert_attachments(
                    check("GET", api, user.realm.host, api_auth(other), 200), 169, private
                )
                assert_attachments(
                    check("GET", api, external.realm.host, api_auth(external), 200), 400, foreign
                )
                check("GET", api, external.realm.host, api_auth(user), 401)

                counts = RealmCount.objects.filter(property="upload_quota_used_bytes::day")
                for realm, end, value, subgroup in (
                    (user.realm, day - timedelta(days=1), 100, None),
                    (user.realm, day, 300, None),
                    (user.realm, day + timedelta(days=1), 900, "ignored"),
                    (external.realm, day + timedelta(days=1), 9000, None),
                ):
                    count = counts.create(
                        property="upload_quota_used_bytes::day",
                        realm=realm,
                        end_time=end,
                        value=value,
                        subgroup=subgroup,
                    )
                    cleanup.callback(count.delete)
                cache_delete(cache_key)
                assert_attachments(check("GET", api, user.realm.host, api_auth(empty), 200), 460)
                unlinked.delete()
                assert_attachments(
                    check("GET", web, user.realm.host, cookie, 200), 430, old, linked
                )


class AttachmentsTests(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        user_profile = self.example_user("cordelia")
        self.attachment = Attachment.objects.create(
            file_name="test.txt",
            path_id="foo/bar/test.txt",
            owner=user_profile,
            realm=user_profile.realm,
            size=512,
        )

    def test_list_by_user(self) -> None:
        user_profile = self.example_user("cordelia")
        self.login_user(user_profile)
        result = self.client_get("/json/attachments")
        response_dict = self.assert_json_success(result)
        attachments = user_attachments(user_profile)
        self.assertEqual(response_dict["attachments"], attachments)

    def test_remove_attachment_exception(self) -> None:
        user_profile = self.example_user("cordelia")
        self.login_user(user_profile)
        with mock.patch(
            "zerver.lib.attachments.delete_message_attachment", side_effect=Exception()
        ):
            result = self.client_delete(f"/json/attachments/{self.attachment.id}")
        self.assert_json_error(
            result, "An error occurred while deleting the attachment. Please try again later."
        )

    @mock.patch("zerver.lib.attachments.delete_message_attachment")
    def test_remove_attachment(self, ignored: Any) -> None:
        user_profile = self.example_user("cordelia")
        self.login_user(user_profile)
        result = self.client_delete(f"/json/attachments/{self.attachment.id}")
        self.assert_json_success(result)
        attachments = user_attachments(user_profile)
        self.assertEqual(attachments, [])

    @mock.patch("zerver.lib.attachments.delete_message_attachment")
    def test_remove_imageattachment(self, ignored: Any) -> None:
        self.login_user(self.example_user("hamlet"))
        with (
            self.thumbnail_formats(ThumbnailFormat("webp", 100, 75, animated=True)),
            get_test_image_file("img.png") as image_file,
        ):
            response = self.assert_json_success(
                self.client_post("/json/user_uploads", {"file": image_file})
            )

        path_id = re.sub(r"/user_uploads/", "", response["url"])
        self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 1)
        self.assertEqual(ImageAttachment.objects.filter(path_id=path_id).count(), 1)
        attachment = Attachment.objects.get(path_id=path_id)
        result = self.client_delete(f"/json/attachments/{attachment.id}")
        self.assert_json_success(result)
        self.assertEqual(Attachment.objects.filter(path_id=path_id).count(), 0)
        self.assertEqual(ImageAttachment.objects.filter(path_id=path_id).count(), 0)

    def test_list_another_user(self) -> None:
        user_profile = self.example_user("iago")
        self.login_user(user_profile)
        result = self.client_get("/json/attachments")
        response_dict = self.assert_json_success(result)
        self.assertEqual(response_dict["attachments"], [])

    def test_remove_another_user(self) -> None:
        user_profile = self.example_user("iago")
        self.login_user(user_profile)
        result = self.client_delete(f"/json/attachments/{self.attachment.id}")
        self.assert_json_error(result, "Invalid attachment")
        user_profile_to_remove = self.example_user("cordelia")
        attachments = user_attachments(user_profile_to_remove)
        self.assertEqual(attachments, [self.attachment.to_dict()])

    def test_list_unauthenticated(self) -> None:
        result = self.client_get("/json/attachments")
        self.assert_json_error(
            result, "Not logged in: API authentication or user session required", status_code=401
        )

    def test_delete_unauthenticated(self) -> None:
        result = self.client_delete(f"/json/attachments/{self.attachment.id}")
        self.assert_json_error(
            result, "Not logged in: API authentication or user session required", status_code=401
        )
