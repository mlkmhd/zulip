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


class Command(ZulipBaseCommand):
    help = "Export the active alert-word Memcached namespace for zulip-http."

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("output", type=Path, help="Destination JSON file")

    @override
    def handle(self, *args: object, **options: object) -> None:
        output = options["output"]
        assert isinstance(output, Path)
        try:
            address = get_rust_alert_word_memcached_address()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        backend = get_cache_backend(None)
        if backend.key_func is not default_key_func:
            raise CommandError("The Rust alert-word cache manifest requires Django's default key function")
        prefixes = get_all_cache_key_prefixes()
        if not prefixes or not all(prefixes):
            raise CommandError("Active Memcached deployment prefixes are unavailable")
        manifest = {
            "address": address,
            "key_prefixes": sorted({backend.make_key(prefix) for prefix in prefixes}),
        }
        with TemporaryDirectory(dir=output.parent) as staging:
            staged_file = Path(staging) / output.name
            staged_file.write_bytes(orjson.dumps(manifest, option=orjson.OPT_APPEND_NEWLINE))
            staged_file.replace(output)
