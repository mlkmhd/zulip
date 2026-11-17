from pathlib import Path
from tempfile import TemporaryDirectory

import orjson
from django.conf import settings
from django.core.management.base import CommandError, CommandParser
from typing_extensions import override

from zerver.lib.management import ZulipBaseCommand
from zerver.models import Realm
from zerver.tornado.sharding import get_realm_tornado_ports, notify_tornado_queue_name


class Command(ZulipBaseCommand):
    help = "Export the resolved Tornado notification queues for zulip-http."

    @override
    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("output", type=Path, help="Destination JSON file")

    @override
    def handle(self, *args: object, **options: object) -> None:
        output = options["output"]
        assert isinstance(output, Path)
        configured = set(settings.TORNADO_PORTS)
        if not configured or any(not 1 <= port <= 65535 for port in configured):
            raise CommandError("Configure valid Tornado ports before exporting notification queues")

        realm_queues = {}
        for realm in Realm.objects.order_by("id").iterator():
            ports = get_realm_tornado_ports(realm)
            if not ports or not configured.issuperset(ports):
                raise CommandError(f"Realm {realm.id} routes to an unconfigured Tornado process")
            realm_queues[str(realm.id)] = [notify_tornado_queue_name(port) for port in ports]

        with TemporaryDirectory(dir=output.parent) as staging:
            staged_file = Path(staging) / output.name
            staged_file.write_bytes(
                orjson.dumps({"realm_queues": realm_queues}, option=orjson.OPT_APPEND_NEWLINE)
            )
            staged_file.replace(output)
