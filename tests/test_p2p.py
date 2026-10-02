"""Loopback integration tests: run with python -m unittest discover -s tests -v."""

import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from p2p_node import P2PNode  # noqa: E402
from protocol import receive_message, send_message  # noqa: E402


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for network event")


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.nodes = []
        self.events = {}

    def node(self, name):
        events = []
        port = free_port()
        node = P2PNode(name, port, Path(self.temp.name) / name,
                       lambda kind, info: events.append((kind, info)))
        node.start()
        self.nodes.append(node)
        self.events[name] = events
        self.addCleanup(node.stop)
        return node

    def test_three_peers_text_and_binary_files(self):
        alice = self.node("Alice")
        bob = self.node("Bob")
        charlie = self.node("Charlie")
        bob.connect("127.0.0.1", alice.port)
        charlie.connect("127.0.0.1", alice.port)
        charlie.connect("127.0.0.1", bob.port)
        wait_for(lambda: len(alice.peers()) == len(bob.peers()) == len(charlie.peers()) == 2)

        alice.send_text(bob.peer_id, "Hello Bob")
        bob.send_text(alice.peer_id, "Hi Alice")
        charlie.send_text(alice.peer_id, "Hello from Charlie")
        wait_for(lambda: sum(kind == "text" for kind, _ in self.events["Alice"]) == 2)
        wait_for(lambda: any(kind == "text" and info["message"] == "Hello Bob"
                             for kind, info in self.events["Bob"]))

        source = Path(self.temp.name) / "sample.bin"
        data = os.urandom(256 * 1024 + 17)
        source.write_bytes(data)
        alice.send_file(charlie.peer_id, source)
        wait_for(lambda: any(kind == "file_received" and info["filename"] == "sample.bin"
                             for kind, info in self.events["Charlie"]))
        self.assertEqual((Path(self.temp.name) / "Charlie" / "sample.bin").read_bytes(), data)

        # Same basename must not overwrite a previously received file.
        alice.send_file(charlie.peer_id, source)
        wait_for(lambda: any(kind == "file_received" and info["filename"] == "sample (1).bin"
                             for kind, info in self.events["Charlie"]))
        self.assertEqual((Path(self.temp.name) / "Charlie" / "sample (1).bin").read_bytes(), data)

        empty = Path(self.temp.name) / "empty.txt"
        empty.write_bytes(b"")
        bob.send_file(charlie.peer_id, empty)
        wait_for(lambda: any(kind == "file_received" and info["filename"] == "empty.txt"
                             for kind, info in self.events["Charlie"]))
        self.assertEqual((Path(self.temp.name) / "Charlie" / "empty.txt").stat().st_size, 0)

    def test_disconnection_removes_peer_and_rejects_bad_input(self):
        alice = self.node("Alice")
        bob = self.node("Bob")
        bob.connect("127.0.0.1", alice.port)
        wait_for(lambda: len(alice.peers()) == 1)
        with self.assertRaises(ValueError):
            alice.send_text("unknown", "hi")
        with self.assertRaises(ValueError):
            alice.connect("not-an-ip", bob.port)
        with self.assertRaises(ValueError):
            alice.connect("127.0.0.1", 0)
        bob.stop()
        wait_for(lambda: not alice.peers())

    def test_invalid_file_size_closes_only_that_connection(self):
        alice = self.node("Alice")
        raw = socket.create_connection(("127.0.0.1", alice.port), timeout=2)
        self.addCleanup(raw.close)
        send_message(raw, {"type": "hello", "peer_id": "test-remote",
                           "peer_name": "Remote", "port": 12345})
        self.assertEqual(receive_message(raw)["type"], "hello_ack")
        wait_for(lambda: len(alice.peers()) == 1)
        send_message(raw, {"type": "file", "filename": "../bad.bin", "filesize": -1})
        wait_for(lambda: not alice.peers())
        self.assertFalse((Path(self.temp.name) / "Alice" / "bad.bin").exists())


if __name__ == "__main__":
    unittest.main()
