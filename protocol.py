"""Small length-prefixed JSON protocol used by every TCP connection.

Each control message is [4-byte unsigned network-order length][UTF-8 JSON].
A file control message is followed by exactly ``filesize`` unframed bytes.
Only the connection's reader consumes incoming bytes; its send lock keeps
outgoing file bytes adjacent to their metadata.
"""

import json
import struct


MAX_JSON_BYTES = 64 * 1024


class ProtocolError(Exception):
    """A remote peer sent an invalid or incomplete protocol message."""


def receive_exact(sock, count):
    """Read exactly count bytes, or raise EOFError if the peer closes."""
    if count < 0:
        raise ValueError("count must be nonnegative")
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(min(64 * 1024, count - len(data)))
        if not chunk:
            raise EOFError("peer disconnected before transfer finished")
        data.extend(chunk)
    return bytes(data)


def send_message(sock, message):
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("message exceeds 64 KiB")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def receive_message(sock):
    header = receive_exact(sock, 4)
    size = struct.unpack("!I", header)[0]
    if not 0 < size <= MAX_JSON_BYTES:
        raise ProtocolError("invalid JSON message length")
    try:
        message = json.loads(receive_exact(sock, size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid JSON message") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("message must be an object with a type")
    return message
