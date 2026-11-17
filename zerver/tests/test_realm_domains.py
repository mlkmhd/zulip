import os
from base64 import b64encode
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import orjson
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import LiveServerTestCase
from typing_extensions import override

from zerver.actions.create_realm import do_create_realm
from zerver.actions.realm_domains import do_change_realm_domain, do_remove_realm_domain
from zerver.actions.realm_settings import do_set_realm_property
from zerver.lib.domains import validate_domain
from zerver.lib.email_validation import email_allowed_for_realm
from zerver.lib.test_classes import ZulipTestCase, ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.models import RealmDomain, UserProfile
from zerver.models.realms import DomainNotAllowedForRealmError, get_realm, get_realm_domains


class RealmDomainReadHTTPTest(ZulipTransactionTestCase, LiveServerTestCase):
    def test_list_realm_domains_parity_with_rust(self) -> None:
        user = self.example_user("hamlet")
        other_user = self.mit_user("sipbtest")
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
            added = RealmDomain.objects.create(
                realm=user.realm, domain="zulip-domain-parity.example", allow_subdomains=True
            )
            cleanup.callback(added.delete)
            other = RealmDomain.objects.create(
                realm=other_user.realm, domain="other-domain-parity.example"
            )
            cleanup.callback(other.delete)

            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            environment = {
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                    settings.AUTHENTICATION_BACKENDS
                ).decode(),
            }
            service = (
                rust_http_service(environment)
                if os.environ.get("ZULIP_RUST_HTTP_BIN")
                else nullcontext(None)
            )
            with service as rust_port:

                def check(
                    method: str, path: str, host: str, headers: dict[str, str], status: int
                ) -> bytes:
                    django = request_test_http(django_port, method, host, headers, path)
                    self.assertEqual(django[0], status, django[1])
                    if rust_port is not None:
                        rust = request_test_http(rust_port, method, host, headers, path)
                        self.assertEqual((rust[0], rust[2]), (django[0], django[2]))
                        if method == "GET" and status == 200:
                            # The list has no ordering guarantee.
                            rust_body, django_body = orjson.loads(rust[1]), orjson.loads(django[1])
                            self.assertCountEqual(
                                rust_body.pop("domains"), django_body.pop("domains")
                            )
                            self.assertEqual(rust_body, django_body)
                        else:
                            self.assertEqual(rust[1], django[1])
                    return django[1]

                api = "/api/v1/realm/domains"
                web = "/json/realm/domains"
                cookie = {"Cookie": f"{settings.SESSION_COOKIE_NAME}={session.session_key}"}
                for path, credentials in ((api, api_auth(user)), (web, cookie)):
                    with self.subTest(path=path):
                        data = orjson.loads(check("GET", path, user.realm.host, credentials, 200))
                        self.assertCountEqual(data["domains"], get_realm_domains(user.realm))
                        self.assertNotIn(
                            other.domain, [entry["domain"] for entry in data["domains"]]
                        )
                        self.assertEqual(
                            check("HEAD", path, user.realm.host, credentials, 200), b""
                        )
                        check("GET", path, user.realm.host, {}, 401)
                check("GET", api, other_user.realm.host, api_auth(user), 401)
                data = orjson.loads(
                    check("GET", api, other_user.realm.host, api_auth(other_user), 200)
                )
                self.assertCountEqual(data["domains"], get_realm_domains(other_user.realm))
                self.assertNotIn(added.domain, [entry["domain"] for entry in data["domains"]])

                added.allow_subdomains = False
                added.save(update_fields=["allow_subdomains"])
                data = orjson.loads(check("GET", web, user.realm.host, cookie, 200))
                self.assertIn({"domain": added.domain, "allow_subdomains": False}, data["domains"])


class RealmDomainTest(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        realm = get_realm("zulip")
        do_set_realm_property(realm, "emails_restricted_to_domains", True, acting_user=None)

    def test_list_realm_domains(self) -> None:
        self.login("iago")
        realm = get_realm("zulip")
        RealmDomain.objects.create(realm=realm, domain="acme.com", allow_subdomains=True)
        result = self.client_get("/json/realm/domains")
        received = self.assert_json_success(result)["domains"]
        expected = [
            {"domain": "zulip.com", "allow_subdomains": False},
            {"domain": "acme.com", "allow_subdomains": True},
        ]
        self.assertEqual(received, expected)

    def test_not_realm_owner(self) -> None:
        self.login("iago")
        result = self.client_post("/json/realm/domains")
        self.assert_json_error(result, "Must be an organization owner")
        result = self.client_patch("/json/realm/domains/15")
        self.assert_json_error(result, "Must be an organization owner")
        result = self.client_delete("/json/realm/domains/15")
        self.assert_json_error(result, "Must be an organization owner")

    def test_create_realm_domain(self) -> None:
        self.login("desdemona")
        data = {
            "domain": "",
            "allow_subdomains": orjson.dumps(True).decode(),
        }
        result = self.client_post("/json/realm/domains", info=data)
        self.assert_json_error(result, "Invalid domain: Domain can't be empty.")

        data["domain"] = "acme.com"
        result = self.client_post("/json/realm/domains", info=data)
        self.assert_json_success(result)
        realm = get_realm("zulip")
        self.assertTrue(
            RealmDomain.objects.filter(
                realm=realm, domain="acme.com", allow_subdomains=True
            ).exists()
        )

        result = self.client_post("/json/realm/domains", info=data)
        self.assert_json_error(
            result, "The domain acme.com is already a part of your organization."
        )

        mit_user_profile = self.mit_user("sipbtest")
        self.login_user(mit_user_profile)

        self.set_user_role(mit_user_profile, UserProfile.ROLE_REALM_OWNER)

        result = self.client_post(
            "/json/realm/domains", info=data, HTTP_HOST=mit_user_profile.realm.host
        )
        self.assert_json_success(result)

    def test_patch_realm_domain(self) -> None:
        self.login("desdemona")
        realm = get_realm("zulip")
        RealmDomain.objects.create(realm=realm, domain="acme.com", allow_subdomains=False)
        data = {
            "allow_subdomains": orjson.dumps(True).decode(),
        }
        url = "/json/realm/domains/acme.com"
        result = self.client_patch(url, data)
        self.assert_json_success(result)
        self.assertTrue(
            RealmDomain.objects.filter(
                realm=realm, domain="acme.com", allow_subdomains=True
            ).exists()
        )

        url = "/json/realm/domains/non-existent.com"
        result = self.client_patch(url, data)
        self.assertEqual(result.status_code, 400)
        self.assert_json_error(result, "No entry found for domain non-existent.com.")

    def test_delete_realm_domain(self) -> None:
        self.login("desdemona")
        realm = get_realm("zulip")
        RealmDomain.objects.create(realm=realm, domain="acme.com")
        result = self.client_delete("/json/realm/domains/non-existent.com")
        self.assertEqual(result.status_code, 400)
        self.assert_json_error(result, "No entry found for domain non-existent.com.")

        result = self.client_delete("/json/realm/domains/acme.com")
        self.assert_json_success(result)
        self.assertFalse(RealmDomain.objects.filter(domain="acme.com").exists())
        self.assertTrue(realm.emails_restricted_to_domains)

    def test_delete_all_realm_domains(self) -> None:
        self.login("iago")
        realm = get_realm("zulip")
        query = RealmDomain.objects.filter(realm=realm)

        self.assertTrue(realm.emails_restricted_to_domains)
        for realm_domain in query.all():
            do_remove_realm_domain(realm_domain, acting_user=None)
        self.assertEqual(query.count(), 0)
        # Deleting last realm_domain should set `emails_restricted_to_domains` to False.
        # This should be tested on a fresh instance, since the cached objects
        # would not be updated.
        self.assertFalse(get_realm("zulip").emails_restricted_to_domains)

    def test_email_allowed_for_realm(self) -> None:
        realm1 = do_create_realm("testrealm1", "Test Realm 1", emails_restricted_to_domains=True)
        realm2 = do_create_realm("testrealm2", "Test Realm 2", emails_restricted_to_domains=True)

        realm_domain = RealmDomain.objects.create(
            realm=realm1, domain="test1.com", allow_subdomains=False
        )
        RealmDomain.objects.create(realm=realm2, domain="test2.test1.com", allow_subdomains=True)

        email_allowed_for_realm("user@test1.com", realm1)
        with self.assertRaises(DomainNotAllowedForRealmError):
            email_allowed_for_realm("user@test2.test1.com", realm1)
        email_allowed_for_realm("user@test2.test1.com", realm2)
        email_allowed_for_realm("user@test3.test2.test1.com", realm2)
        with self.assertRaises(DomainNotAllowedForRealmError):
            email_allowed_for_realm("user@test3.test1.com", realm2)

        do_change_realm_domain(realm_domain, True, acting_user=None)
        email_allowed_for_realm("user@test1.com", realm1)
        email_allowed_for_realm("user@test2.test1.com", realm1)
        with self.assertRaises(DomainNotAllowedForRealmError):
            email_allowed_for_realm("user@test2.com", realm1)

    def test_realm_realm_domains_uniqueness(self) -> None:
        realm = get_realm("zulip")
        with self.assertRaises(IntegrityError):
            RealmDomain.objects.create(realm=realm, domain="zulip.com", allow_subdomains=True)

    def test_validate_domain(self) -> None:
        invalid_domains = [
            "",
            "test",
            "t.",
            "test.",
            ".com",
            "-test",
            "test...com",
            "test-",
            "test_domain.com",
            "test.-domain.com",
            "a" * 255 + ".com",
        ]
        for domain in invalid_domains:
            with self.assertRaises(ValidationError):
                validate_domain(domain)

        valid_domains = ["acme.com", "x-x.y.3.z"]
        for domain in valid_domains:
            validate_domain(domain)
