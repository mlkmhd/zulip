import os
from base64 import b64encode
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import orjson
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.test import LiveServerTestCase

from zerver.actions.navigation_views import do_add_navigation_view
from zerver.lib.navigation_views import get_navigation_views_for_user
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.models import NavigationView, UserProfile


class NavigationViewReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_get_navigation_views_parity_with_rust(self) -> None:
        user = self.example_user("hamlet")
        other_user = self.example_user("othello")
        django_port = urlsplit(self.live_server_url).port
        assert django_port is not None

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
            other_view = NavigationView.objects.create(
                user=other_user, fragment="narrow/is/mentioned", is_pinned=True
            )
            cleanup.callback(other_view.delete)
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            service = (
                rust_http_service(
                    {
                        "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                        "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                            settings.AUTHENTICATION_BACKENDS
                        ).decode(),
                    }
                )
                if os.environ.get("ZULIP_RUST_HTTP_BIN")
                else nullcontext(None)
            )
            with service as rust_port:
                api = "/api/v1/navigation_views"
                web = "/json/navigation_views"
                cookie = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}

                def check(method: str, path: str, headers: dict[str, str], status: int) -> bytes:
                    django = request_test_http(django_port, method, user.realm.host, headers, path)
                    self.assertEqual(django[0], status, django[1])
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, user.realm.host, headers, path)
                        self.assertEqual((rust[0], rust[2]), (django[0], django[2]))
                        if method == "GET" and status == 200:
                            rust_body, django_body = orjson.loads(rust[1]), orjson.loads(django[1])
                            self.assertCountEqual(
                                rust_body.pop("navigation_views"),
                                django_body.pop("navigation_views"),
                            )
                            self.assertEqual(rust_body, django_body)
                        else:
                            self.assertEqual(rust[1], django[1])
                    return django[1]

                self.assertEqual(
                    orjson.loads(check("GET", api, api_auth(user), 200))["navigation_views"], []
                )
                built_in = NavigationView.objects.create(
                    user=user, fragment="inbox", is_pinned=True
                )
                cleanup.callback(built_in.delete)
                custom = NavigationView.objects.create(
                    user=user, fragment="narrow/is/alerted", name="یادآوری‌ها", is_pinned=False
                )
                cleanup.callback(custom.delete)
                for path, credentials in ((api, api_auth(user)), (web, cookie)):
                    with self.subTest(path=path):
                        data = orjson.loads(check("GET", path, credentials, 200))
                        self.assertCountEqual(
                            data["navigation_views"],
                            [
                                {"fragment": "inbox", "name": None, "is_pinned": True},
                                {
                                    "fragment": custom.fragment,
                                    "name": custom.name,
                                    "is_pinned": False,
                                },
                            ],
                        )
                        self.assertEqual(check("HEAD", path, credentials, 200), b"")
                        check("GET", path, {}, 401)

                data = orjson.loads(check("GET", api, api_auth(other_user), 200))
                self.assertEqual(
                    data["navigation_views"],
                    [{"fragment": other_view.fragment, "name": None, "is_pinned": True}],
                )

                custom.is_pinned = True
                custom.save(update_fields=["is_pinned"])
                data = orjson.loads(check("GET", web, cookie, 200))
                self.assertIn(
                    {"fragment": custom.fragment, "name": custom.name, "is_pinned": True},
                    data["navigation_views"],
                )


class NavigationViewTests(ZulipTestCase):
    def create_example_navigation_view(
        self, user: UserProfile, fragment: str, is_pinned: bool, name: str | None = None
    ) -> str:
        do_add_navigation_view(
            user,
            fragment,
            is_pinned,
            name,
        )
        return fragment

    def test_get_navigation_views(self) -> None:
        """Tests fetching navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)

        result = self.client_get("/json/navigation_views")
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["navigation_views"], 0)

        # Test adding a built-in view and fetching it
        self.create_example_navigation_view(user, fragment="inbox", is_pinned=True)
        result = self.client_get("/json/navigation_views")
        response_dict = self.assert_json_success(result)
        self.assert_length(response_dict["navigation_views"], 1)
        self.assertEqual(response_dict["navigation_views"][0]["fragment"], "inbox")
        self.assertEqual(response_dict["navigation_views"][0]["is_pinned"], True)

    def test_add_navigation_view(self) -> None:
        """Tests creation of navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)

        # Test successful creation
        params = {
            "fragment": "recent",
            "is_pinned": "true",
        }
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_success(result)

        # Test name value for builtin views
        params["name"] = "foo"
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_error(result, "Built-in views cannot have a custom name.")

        # Test empty fragment
        params["fragment"] = ""
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_error(result, "fragment cannot be blank")

        # Test no name value for custom views
        params = {
            "fragment": "narrow/view",
            "is_pinned": "true",
        }
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_error(result, "Custom views must have a valid name.")

        # Test custom view with name value
        params["name"] = "foo"
        result = self.client_post("/json/navigation_views", params)
        self.assert_json_success(result)

        # Test duplicate view
        new_params = {
            "fragment": "recent",
            "is_pinned": "true",
        }
        result = self.client_post("/json/navigation_views", new_params)
        self.assert_json_error(result, "Navigation view already exists.")

        # Test duplicate view by name
        new_params = {
            "fragment": "narorw/is/attachment",
            "is_pinned": "true",
            "name": "foo",
        }
        result = self.client_post("/json/navigation_views", new_params)
        self.assert_json_error(result, "Navigation view already exists.")

    def test_update_navigation_view(self) -> None:
        """Tests updating navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)
        self.create_example_navigation_view(user, fragment="inbox", is_pinned=True)

        # Test successful update
        params = {
            "is_pinned": "false",
        }
        result = self.client_patch("/json/navigation_views/inbox", params)
        self.assert_json_success(result)

        navigation_views = get_navigation_views_for_user(user)
        self.assertEqual(navigation_views[0]["is_pinned"], False)

        params = {
            "is_pinned": "true",
            "name": "Inbox View",
        }
        result = self.client_patch("/json/navigation_views/inbox", params)
        self.assert_json_error(result, "Built-in views cannot have a custom name.")

        # Test with the name for custom views
        self.create_example_navigation_view(
            user, fragment="narrow/is/alerted", is_pinned=True, name="Alert Words"
        )
        params = {
            "is_pinned": "false",
            "name": "Watched Phrases",
        }
        result = self.client_patch("/json/navigation_views/narrow/is/alerted", params)
        self.assert_json_success(result)

        self.create_example_navigation_view(
            user, fragment="narrow/is/attachment", is_pinned=True, name="Attachments"
        )
        params = {
            "is_pinned": "false",
            "name": "Watched Phrases",
        }
        result = self.client_patch("/json/navigation_views/narrow/is/attachment", params)
        self.assert_json_error(result, "Navigation view already exists.")

        params = {
            "is_pinned": "false",
            "name": "New view",
        }
        # Test nonexistent view
        result = self.client_patch("/json/navigation_views/nonexistent", params)
        self.assert_json_error(result, "Navigation view does not exist.", status_code=404)

    def test_remove_navigation_view(self) -> None:
        """Tests removing navigation views."""
        user = self.example_user("hamlet")
        self.login_user(user)
        self.create_example_navigation_view(user, fragment="recent", is_pinned=True)

        # Test successful removal
        result = self.client_delete("/json/navigation_views/recent")
        self.assert_json_success(result)

        navigation_views = get_navigation_views_for_user(user)
        self.assert_length(navigation_views, 0)

        # Test nonexistent view
        result = self.client_delete("/json/navigation_views/nonexistent")
        self.assert_json_error(result, "Navigation view does not exist.", status_code=404)

    def test_navigation_view_permissions(self) -> None:
        """Tests permissions for navigation view operations."""
        hamlet = self.example_user("hamlet")
        othello = self.example_user("othello")
        self.login_user(othello)

        self.create_example_navigation_view(hamlet, fragment="recent", is_pinned=True)

        params = {
            "is_pinned": "false",
        }
        result = self.client_patch("/json/navigation_views/recent", params)
        self.assert_json_error(result, "Navigation view does not exist.", status_code=404)
