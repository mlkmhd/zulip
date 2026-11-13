from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from zerver.lib.test_classes import ZulipTestCase
from zerver.lib.test_helpers import create_s3_buckets, read_test_image_file, use_s3_backend
from zerver.models.realms import get_realm


class ChannelIconTest(ZulipTestCase):
    def image(self) -> SimpleUploadedFile:
        return SimpleUploadedFile(
            "picture.png", read_test_image_file("img.png"), "image/png"
        )

    def test_private_channel_picture_lifecycle(self) -> None:
        admin = self.example_user("iago")
        member = self.example_user("hamlet")
        outsider = self.example_user("cordelia")
        stream = self.make_stream("Private picture", invite_only=True)
        self.subscribe(member, stream.name)
        api_url = f"/api/v1/streams/{stream.id}/icon"

        self.assertEqual(self.api_get(admin, api_url).status_code, 404)
        self.assert_json_error(
            self.api_post(member, api_url, {"file": self.image()}),
            "You do not have permission to administer this channel.",
        )
        with self.capture_send_event_calls(expected_num_events=1) as events:
            result = self.assert_json_success(self.api_post(admin, api_url, {"file": self.image()}))
        first_url = result["icon_url"]
        self.assertEqual(
            events[0]["event"],
            {
                "type": "stream",
                "op": "update",
                "stream_id": stream.id,
                "name": stream.name,
                "property": "icon_url",
                "value": first_url,
            },
        )
        self.assertIn(member.id, events[0]["users"])
        self.assertNotIn(outsider.id, events[0]["users"])

        stream.refresh_from_db()
        self.assertIsNotNone(stream.icon_key)
        channel = self.assert_json_success(self.api_get(member, f"/api/v1/streams/{stream.id}"))
        self.assertEqual(channel["stream"]["icon_url"], first_url)
        subscriptions = self.assert_json_success(self.api_get(member, "/api/v1/users/me/subscriptions"))
        sub = next(sub for sub in subscriptions["subscriptions"] if sub["stream_id"] == stream.id)
        self.assertEqual(sub["icon_url"], first_url)

        self.login("hamlet")
        response = self.client_get(first_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.assert_json_error(self.api_get(outsider, api_url), "Invalid channel ID")
        self.assert_json_error(
            self.api_get(self.mit_user("sipbtest"), api_url, subdomain="zephyr"),
            "Invalid channel ID",
        )

        result = self.assert_json_success(self.api_post(admin, api_url, {"file": self.image()}))
        self.assertNotEqual(result["icon_url"], first_url)
        self.assertEqual(self.client_get(first_url).status_code, 404)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.api_delete(admin, api_url))
        self.assertEqual(events[0]["event"]["property"], "icon_url")
        self.assertIsNone(events[0]["event"]["value"])
        self.assertEqual(self.client_get(result["icon_url"]).status_code, 404)
        stream.refresh_from_db()
        self.assertIsNone(stream.icon_key)

    @use_s3_backend
    def test_picture_on_s3(self) -> None:
        create_s3_buckets(settings.S3_AUTH_UPLOADS_BUCKET)
        stream = self.make_stream("S3 picture")
        admin = self.example_user("iago")
        url = f"/api/v1/streams/{stream.id}/icon"
        self.assert_json_success(self.api_post(admin, url, {"file": self.image()}))

        response = self.api_get(admin, url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_invalid_upload(self) -> None:
        admin = self.example_user("iago")
        stream = self.make_stream("Picture upload validation")
        url = f"/api/v1/streams/{stream.id}/icon"
        self.assert_json_error(self.api_post(admin, url), "You must upload exactly one icon.")
        self.assert_json_error(
            self.api_post(admin, url, {"file": [self.image(), self.image()]}),
            "You must upload exactly one icon.",
        )
        with override_settings(MAX_ICON_FILE_SIZE_MIB=0):
            self.assert_json_error(
                self.api_post(admin, url, {"file": self.image()}),
                "Uploaded file is larger than the allowed limit of 0 MiB",
            )
        self.assert_json_error(
            self.api_post(admin, url, {"file": SimpleUploadedFile("picture.png", b"invalid")}),
            "Could not decode image; did you upload an image file?",
        )
        stream.refresh_from_db()
        self.assertIsNone(stream.icon_key)

    @override_settings(WEB_PUBLIC_STREAMS_ENABLED=True)
    def test_web_public_picture(self) -> None:
        realm = get_realm("zulip")
        realm.enable_spectator_access = True
        realm.save(update_fields=["enable_spectator_access"])
        stream = self.make_stream("Public picture", is_web_public=True)
        admin = self.example_user("iago")
        url = f"/api/v1/streams/{stream.id}/icon"
        result = self.assert_json_success(self.api_post(admin, url, {"file": self.image()}))
        self.assertEqual(self.client_get(result["icon_url"]).status_code, 200)

        stream.is_web_public = False
        stream.save(update_fields=["is_web_public"])
        self.assert_json_error(self.client_get(result["icon_url"]), "Invalid channel ID")
