import os
import select
import socket
import socketserver
import threading
import time
from base64 import b64encode
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from urllib.parse import quote

import orjson
import pika
from django.conf import settings

from zerver.lib.test_classes import ZulipTransactionTestCase
from zerver.lib.test_rust_http import request_test_http, rust_http_service
from zerver.models import Draft


class BrokerRelay(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), BrokerRelayHandler)
        self.lock = threading.Lock()
        self.paused = False
        self.connections: set[tuple[socket.socket, socket.socket]] = set()

    def cut(self) -> None:
        with self.lock:
            self.paused = True
            for pair in self.connections:
                for connection in pair:
                    with suppress(OSError):
                        connection.shutdown(socket.SHUT_RDWR)

    def restore(self) -> None:
        with self.lock:
            self.paused = False


class BrokerRelayHandler(socketserver.BaseRequestHandler):
    server: BrokerRelay
    request: socket.socket

    def handle(self) -> None:
        with (
            suppress(OSError),
            socket.create_connection((settings.RABBITMQ_HOST, settings.RABBITMQ_PORT)) as broker,
        ):
            pair = (self.request, broker)
            with self.server.lock:
                if self.server.paused:
                    return
                self.server.connections.add(pair)
            try:
                while True:
                    readable, _, _ = select.select(pair, [], [])
                    for source in readable:
                        content = source.recv(65536)
                        if not content:
                            return
                        destination = broker if source is self.request else self.request
                        destination.sendall(content)
            finally:
                with self.server.lock:
                    self.server.connections.discard(pair)


@skipUnless(
    os.environ.get("ZULIP_RUST_HTTP_BIN")
    and os.environ.get("ZULIP_RUST_BROKER_RECOVERY_TEST_VHOST"),
    "Rust binary and dedicated RabbitMQ recovery test vhost are required",
)
class RustBrokerRecoveryTest(ZulipTransactionTestCase):
    def test_rust_recovers_and_only_accepted_write_publishes(self) -> None:
        vhost = os.environ["ZULIP_RUST_BROKER_RECOVERY_TEST_VHOST"]
        assert vhost.startswith("zulip-rust-test-")
        password = settings.RABBITMQ_PASSWORD
        assert password is not None
        credentials = f"{quote(settings.RABBITMQ_USERNAME, safe='')}:{quote(password, safe='')}"
        encoded_vhost = quote(vhost, safe="")
        direct = f"amqp://{credentials}@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{encoded_vhost}"
        user = self.example_user("hamlet")
        draft = Draft.objects.create(
            user_profile=user,
            content="Draft during a broker outage",
            last_edit_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.addCleanup(Draft.objects.filter(id=draft.id).delete)
        target = f"/api/v1/drafts/{draft.id}"
        auth = b64encode(f"{user.delivery_email}:{user.api_key}".encode()).decode()

        with (
            BrokerRelay() as relay,
            pika.BlockingConnection(pika.URLParameters(direct)) as broker,
            TemporaryDirectory() as directory,
        ):
            secret = Path(directory) / "secret"
            secret.write_text(settings.SECRET_KEY)
            relay_port = relay.server_address[1]
            environment = {
                "ZULIP_RUST_AMQP_URL": f"amqp://{credentials}@127.0.0.1:{relay_port}/{encoded_vhost}",
                "ZULIP_RUST_DRAFT_DELETES": "1",
                "ZULIP_RUST_DJANGO_SECRET_KEY_FILE": str(secret),
                "ZULIP_RUST_DJANGO_AUTH_BACKENDS": orjson.dumps(
                    settings.AUTHENTICATION_BACKENDS
                ).decode(),
            }
            channel = broker.channel()
            for queue in ("notify_tornado", "user_activity"):
                channel.queue_declare(queue, durable=True)
                channel.queue_purge(queue)
            thread = threading.Thread(target=relay.serve_forever)
            thread.start()
            try:
                with rust_http_service(environment) as port:

                    def wait_for_health(expected: int) -> None:
                        deadline = time.monotonic() + 20
                        while True:
                            status, body, *_ = request_test_http(
                                port,
                                "GET",
                                user.realm.host,
                                {},
                                "/health/rust-alert-words",
                                timeout=10,
                            )
                            if status == expected:
                                self.assertEqual(orjson.loads(body), {"ready": expected == 200})
                                return
                            self.assertLess(time.monotonic(), deadline, (status, expected))
                            time.sleep(0.1)

                    wait_for_health(200)
                    relay.cut()
                    wait_for_health(503)
                    status, body, *_ = request_test_http(
                        port,
                        "DELETE",
                        user.realm.host,
                        {"Authorization": f"Basic {auth}"},
                        target,
                        timeout=10,
                    )
                    self.assertEqual(status, 503, body)
                    self.assertTrue(Draft.objects.filter(id=draft.id).exists())
                    self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
                    self.assertIsNone(channel.basic_get("user_activity", auto_ack=True)[2])

                    # Exercise a downtime longer than Lapin's automatic recovery window.
                    time.sleep(
                        float(os.environ.get("ZULIP_RUST_BROKER_RECOVERY_TEST_DOWNTIME", "90"))
                    )
                    relay.restore()
                    wait_for_health(200)
                    status, body, *_ = request_test_http(
                        port,
                        "DELETE",
                        user.realm.host,
                        {"Authorization": f"Basic {auth}"},
                        target,
                        timeout=10,
                    )
                    self.assertEqual(status, 200, body)
                    self.assertFalse(Draft.objects.filter(id=draft.id).exists())
                    _, _, event = channel.basic_get("notify_tornado", auto_ack=True)
                    assert event is not None
                    self.assertEqual(
                        orjson.loads(event),
                        {
                            "event": {"type": "drafts", "op": "remove", "draft_id": draft.id},
                            "users": [user.id],
                        },
                    )
                    self.assertIsNone(channel.basic_get("notify_tornado", auto_ack=True)[2])
            finally:
                relay.cut()
                relay.shutdown()
                thread.join()
                for queue in ("notify_tornado", "user_activity"):
                    channel.queue_purge(queue)
