import os
from http.client import HTTPConnection
from typing import TYPE_CHECKING
from unittest import mock

from django.http import HttpResponse

from zerver.lib.compatibility import (
    find_mobile_os,
    is_outdated_desktop_app,
    is_pronouns_field_type_supported,
    version_lt,
)
from zerver.lib.test_classes import ZulipTestCase

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestHttpResponse


class VersionTest(ZulipTestCase):
    data = [
        case.split()
        for case in """
        1.2.3    <  1.2.4
        1.2.3    =  1.2.3
        1.4.1    >  1.2.3
        1.002a   =  1.2a
        1.2      <  1.2.3
        1.2.3    ?  1.2-dev
        1.2-dev  ?  1.2a
        1.2a     ?  1.2rc3
        1.2rc3   ?  1.2
        1.2      ?  1.2-g0f1e2d3c4
        10.1     >  1.2
        0.17.18  <  16.2.96
        9.10.11  <  16.2.96
        15.1.95  <  16.2.96
        16.2.96  =  16.2.96
        20.0.103 >  16.2.96
    """.strip().split("\n")
    ] + [
        ["", "?", "1"],
        ["", "?", "a"],
    ]

    def test_version_lt(self) -> None:
        for ver1, cmp, ver2 in self.data:
            msg = f"expected {ver1} {cmp} {ver2}"
            if cmp == "<":
                self.assertTrue(version_lt(ver1, ver2), msg=msg)
                self.assertFalse(version_lt(ver2, ver1), msg=msg)
            elif cmp == "=":
                self.assertFalse(version_lt(ver1, ver2), msg=msg)
                self.assertFalse(version_lt(ver2, ver1), msg=msg)
            elif cmp == ">":
                self.assertFalse(version_lt(ver1, ver2), msg=msg)
                self.assertTrue(version_lt(ver2, ver1), msg=msg)
            elif cmp == "?":
                self.assertIsNone(version_lt(ver1, ver2), msg=msg)
                self.assertIsNone(version_lt(ver2, ver1), msg=msg)
            else:
                raise AssertionError  # nocoverage

    mobile_os_data = [
        case.split(None, 1)
        for case in """
      android ZulipMobile/1.2.3 (Android 4.5)
      ios     ZulipMobile/1.2.3 (iPhone OS 2.1)
      ios     ZulipMobile/1.2.3 (iOS 6)
      None    ZulipMobile/1.2.3 (Windows 8)
    """.strip().split("\n")
    ]

    def test_find_mobile_os(self) -> None:
        for expected_, user_agent in self.mobile_os_data:
            expected = None if expected_ == "None" else expected_
            self.assertEqual(find_mobile_os(user_agent), expected, msg=user_agent)


class CompatibilityTest(ZulipTestCase):
    data = [
        case.split(None, 1)
        for case in """
      old ZulipInvalid/5.0
      ok  ZulipMobile/5.0
      ok  ZulipMobile/5.0 (iOS 11)
      ok  ZulipMobile/5.0 (Androidish 9)
      old ZulipMobile/5.0 (Android 9)
      old ZulipMobile/15.1.95 (Android 9)
      old ZulipMobile/16.1.94 (Android 9)
      ok  ZulipMobile/16.2.96 (Android 9)
      ok  ZulipMobile/20.0.103 (Android 9)

      ok  ZulipMobile/0.7.1.1 (iOS 11.4)
      old ZulipMobile/1.0.13 (Android 9)
      ok  ZulipMobile/17.1.98 (iOS 12.0)
      ok  ZulipMobile/19.2.102 (Android 6.0)
      ok  ZulipMobile/1 CFNetwork/974.2.1 Darwin/18.0.0
      ok  ZulipMobile/20.0.103 (Android 6.0.1)
      ok  ZulipMobile/20.0.103 (iOS 12.1)
    """.strip().split("\n")
        if case
    ]

    def request_compatibility(
        self, headers: dict[str, str], *, method: str = "GET"
    ) -> "HttpResponse | TestHttpResponse":
        rust_port = os.environ.get("ZULIP_RUST_COMPATIBILITY_TEST_PORT")
        if rust_port is None:
            return self.client.generic(method, "/compatibility", headers=headers)

        # Keep the existing assertions and send the request over HTTP to Rust.
        connection = HTTPConnection("127.0.0.1", int(rust_port), timeout=5)
        try:
            connection.request(
                method, "/compatibility", headers={"Host": f"localhost:{rust_port}", **headers}
            )
            reply = connection.getresponse()
            return HttpResponse(reply.read(), status=reply.status, headers=reply.getheaders())
        finally:
            connection.close()

    def test_compatibility_without_user_agent(self) -> None:
        result = self.request_compatibility({})
        self.assert_json_error(result, "User-Agent header missing from request")

    def test_compatibility(self) -> None:
        for expected, user_agent in self.data:
            result = self.request_compatibility({"User-Agent": user_agent})
            if expected == "ok":
                self.assert_json_success(result)
            elif expected == "old":
                self.assert_json_error(result, "Client is too old")
            else:
                raise AssertionError  # nocoverage

    def test_compatibility_localizes_only_the_missing_header_error(self) -> None:
        french = {"Accept-Language": "de;q=0.5, fr-CA;q=0.8"}
        result = self.request_compatibility(french)
        self.assert_json_error(result, "En-tête User-Agent absent de la requête")
        self.assertEqual(result["Content-Language"], "fr")

        result = self.request_compatibility({**french, "User-Agent": "ZulipInvalid/5.0"})
        self.assert_json_error(result, "Client is too old")

        result = self.request_compatibility({**french, "Cookie": "django_language=pt"})
        self.assert_json_error(result, "Cabeçalho User-Agent em falta no pedido")
        self.assertEqual(result["Content-Language"], "pt")

    def test_compatibility_head(self) -> None:
        result = self.request_compatibility({"User-Agent": "ZulipMobile/20.0.103"}, method="HEAD")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["Content-Type"], "application/json")
        self.assertEqual(result.content, b"")

    @mock.patch("zerver.lib.compatibility.DESKTOP_MINIMUM_VERSION", "5.0.0")
    @mock.patch("zerver.lib.compatibility.DESKTOP_WARNING_VERSION", "5.2.0")
    def test_insecure_desktop_app(self) -> None:
        self.assertEqual(is_outdated_desktop_app("ZulipDesktop/0.5.2 (Mac)"), (True, True, True))
        self.assertEqual(
            is_outdated_desktop_app(
                "ZulipElectron/2.3.82 Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Zulip/2.3.82 Chrome/61.0.3163.100 Electron/2.0.9 Safari/537.36"
            ),
            (True, True, True),
        )
        self.assertEqual(
            is_outdated_desktop_app(
                "ZulipElectron/4.0.0 Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Zulip/4.0.3 Chrome/66.0.3359.181 Electron/3.1.10 Safari/537.36"
            ),
            (True, True, False),
        )

        self.assertEqual(
            is_outdated_desktop_app(
                "ZulipElectron/4.0.3 Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Zulip/4.0.3 Chrome/66.0.3359.181 Electron/3.1.10 Safari/537.36"
            ),
            (True, True, False),
        )

        # Verify what happens if DESKTOP_MINIMUM_VERSION < v < DESKTOP_WARNING_VERSION
        with mock.patch("zerver.lib.compatibility.DESKTOP_MINIMUM_VERSION", "4.0.3"):
            self.assertEqual(
                is_outdated_desktop_app(
                    "ZulipElectron/4.0.3 Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Zulip/4.0.3 Chrome/66.0.3359.181 Electron/3.1.10 Safari/537.36"
                ),
                (True, False, False),
            )

        self.assertEqual(
            is_outdated_desktop_app(
                "ZulipElectron/5.2.0 Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Zulip/5.2.0 Chrome/80.0.3987.165 Electron/8.2.5 Safari/537.36"
            ),
            (False, False, False),
        )

        self.assertEqual(
            is_outdated_desktop_app(
                "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36"
            ),
            (False, False, False),
        )

        self.assertEqual(is_outdated_desktop_app(""), (False, False, False))

    def test_is_pronouns_field_type_supported(self) -> None:
        self.assertEqual(
            is_pronouns_field_type_supported("ZulipMobile/20.0.103 (Android 6.0.1)"), False
        )
        self.assertEqual(is_pronouns_field_type_supported("ZulipMobile/20.0.103 (iOS 12.0)"), False)

        self.assertEqual(
            is_pronouns_field_type_supported("ZulipMobile/27.191 (Android 6.0.1)"), False
        )
        self.assertEqual(is_pronouns_field_type_supported("ZulipMobile/27.191 (iOS 12.0)"), False)

        self.assertEqual(
            is_pronouns_field_type_supported("ZulipMobile/27.192 (Android 6.0.1)"), True
        )
        self.assertEqual(is_pronouns_field_type_supported("ZulipMobile/27.192 (iOS 12.0)"), True)

        self.assertEqual(
            is_pronouns_field_type_supported(
                "ZulipElectron/5.2.0 Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Zulip/5.2.0 Chrome/80.0.3987.165 Electron/8.2.5 Safari/537.36"
            ),
            True,
        )
