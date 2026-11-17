from pathlib import Path
from tempfile import TemporaryDirectory

import orjson
from django.core.cache.backends.base import default_key_func
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError, CommandParser
from typing_extensions import override

from zerver.lib.cache import get_all_cache_key_prefixes, get_cache_backend
from zerver.lib.management import ZulipBaseCommand
from zerver.models.alert_words import get_rust_alert_word_memcached_address


def lowercase_tables() -> dict[str, object]:
    mappings: dict[str, str] = {}
    cased: list[list[int]] = []
    case_ignorable: list[list[int]] = []

    def extend_ranges(ranges: list[list[int]], codepoint: int) -> None:
        if ranges and ranges[-1][1] + 1 == codepoint:
            ranges[-1][1] = codepoint
        else:
            ranges.append([codepoint, codepoint])

    for codepoint in range(0x110000):
        if 0xD800 <= codepoint <= 0xDFFF:
            continue
        char = chr(codepoint)
        lowered = char.lower()
        if lowered != char:
            mappings[char] = lowered
        # Python skips case-ignorable characters when testing whether sigma is final.
        # A trailing A reveals which characters allow the test to look past them.
        if ("AΣ" + char).lower()[1] == "σ":
            extend_ranges(cased, codepoint)
        elif ("AΣ" + char + "A").lower()[1] == "σ":
            extend_ranges(case_ignorable, codepoint)
    return {"mappings": mappings, "cased": cased, "case_ignorable": case_ignorable}


def publish_json(output: Path, data: dict[str, object]) -> None:
    with TemporaryDirectory(dir=output.parent) as staging:
        staged_file = Path(staging) / output.name
        staged_file.write_bytes(orjson.dumps(data, option=orjson.OPT_APPEND_NEWLINE))
        staged_file.replace(output)


class Command(ZulipBaseCommand):
    help = "Export the active alert-word Memcached namespace for zulip-http."

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("output", type=Path, help="Destination JSON file")
        parser.add_argument(
            "--lowercase-output", type=Path, help="Also export Python's Unicode lowercase tables"
        )

    @override
    def handle(self, *args: object, **options: object) -> None:
        output = options["output"]
        assert isinstance(output, Path)
        lowercase_output = options["lowercase_output"]
        assert lowercase_output is None or isinstance(lowercase_output, Path)
        if lowercase_output is not None and lowercase_output.resolve() == output.resolve():
            raise CommandError("The cache manifest and lowercase tables require different paths")
        try:
            address = get_rust_alert_word_memcached_address()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        backend = get_cache_backend(None)
        if backend.key_func is not default_key_func:
            raise CommandError(
                "The Rust alert-word cache manifest requires Django's default key function"
            )
        prefixes = get_all_cache_key_prefixes()
        if not prefixes or not all(prefixes):
            raise CommandError("Active Memcached deployment prefixes are unavailable")
        manifest: dict[str, object] = {
            "address": address,
            "key_prefixes": sorted({backend.make_key(prefix) for prefix in prefixes}),
        }
        if lowercase_output is not None:
            publish_json(lowercase_output, lowercase_tables())
        publish_json(output, manifest)
