"""Direct, threaded TCP connections between named peers."""

import ipaddress
import os
import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from protocol import ProtocolError, receive_message, send_message


CHUNK_SIZE = 64 * 1024
MAX_FILE_BYTES = 1024 ** 4  # A safety bound; files are streamed, not buffered.


@dataclass
class PeerConnection:
    peer_id: str
    name: str
    address: str
    port: int
    sock: socket.socket = field(repr=False)
    send_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def validate_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("port must be a number from 1 to 65535") from exc
    if isinstance(value, bool) or not 1 <= port <= 65535:
        raise ValueError("port must be a number from 1 to 65535")
    return port


def validate_ip(value):
    try:
        address = ipaddress.ip_address(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError("enter a valid IPv4 address") from exc
    if address.version != 4:
        raise ValueError("enter a valid IPv4 address")
    return str(address)


def _clean_filename(value):
    if not isinstance(value, str):
        raise ProtocolError("invalid filename")
    name = value.replace("\\", "/").split("/")[-1].strip()
    if not name or name in (".", "..") or "\x00" in name:
        raise ProtocolError("invalid filename")
    # Windows rejects these characters even when received from another OS.
    name = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in name)
    name = name.rstrip(" .")[:200]
    if not name:
        raise ProtocolError("invalid filename")
    return name


class P2PNode:
    """One node is both a listening server and an outgoing TCP client.

    ``on_event(kind, details)`` is called from worker threads, so GUI code must
    forward it to its main thread before updating widgets.
    """

    def __init__(self, name, port, downloads_dir, on_event=None):
        self.name = name.strip()
        if not self.name or len(self.name) > 60:
            raise ValueError("peer name must contain 1 to 60 characters")
        self.port = validate_port(port)
        self.peer_id = uuid.uuid4().hex[:8]
        self.downloads_dir = Path(downloads_dir)
        self.on_event = on_event or (lambda kind, details: None)
        self._server = None
        self._peers = {}
        self._lock = threading.RLock()
        self._stopping = threading.Event()

    def _emit(self, kind, **details):
        try:
            self.on_event(kind, details)
        except Exception:
            # A UI callback must not terminate network threads.
            pass

    def start(self):
        if self._server is not None:
            raise RuntimeError("peer is already running")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", self.port))
            server.listen()
            server.settimeout(0.5)
        except Exception:
            server.close()
            raise
        self._stopping.clear()
        self._server = server
        threading.Thread(target=self._accept_loop, name="accept-peers", daemon=True).start()
        self._emit("started", name=self.name, peer_id=self.peer_id, port=self.port)

    def stop(self):
        if self._server is None:
            return
        self._stopping.set()
        self._server.close()
        self._server = None
        with self._lock:
            peers = list(self._peers.values())
            self._peers.clear()
        for peer in peers:
            self._close_socket(peer.sock)
        self._emit("stopped")

    @staticmethod
    def _close_socket(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def _accept_loop(self):
        server = self._server
        while not self._stopping.is_set():
            try:
                sock, address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._serve_incoming,
                args=(sock, address[0]),
                name="incoming-peer",
                daemon=True,
            ).start()

    def _hello(self, kind):
        return {"type": kind, "peer_id": self.peer_id, "peer_name": self.name, "port": self.port}

    def _identify(self, message, expected_type):
        if message.get("type") != expected_type:
            raise ProtocolError("expected " + expected_type)
        peer_id = message.get("peer_id")
        name = message.get("peer_name")
        port = message.get("port")
        if not isinstance(peer_id, str) or not 1 <= len(peer_id) <= 64:
            raise ProtocolError("invalid peer ID")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ProtocolError("invalid peer name")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ProtocolError("invalid peer port")
        if peer_id == self.peer_id:
            raise ProtocolError("cannot connect to yourself")
        return peer_id, name.strip(), port

    def _register(self, sock, address, identity):
        peer_id, name, port = identity
        with self._lock:
            if self._stopping.is_set():
                raise ConnectionError("peer stopped")
            if peer_id in self._peers:
                raise ConnectionError("this peer is already connected")
            peer = PeerConnection(peer_id, name, address, port, sock)
            self._peers[peer_id] = peer
        self._emit("connected", peer_id=peer_id, name=name, address=address, port=port)
        return peer

    def _serve_incoming(self, sock, address):
        peer = None
        try:
            sock.settimeout(10)
            identity = self._identify(receive_message(sock), "hello")
            send_message(sock, self._hello("hello_ack"))
            sock.settimeout(None)
            peer = self._register(sock, address, identity)
            self._read_loop(peer)
        except (OSError, EOFError, ProtocolError, ConnectionError, ValueError) as exc:
            if not self._stopping.is_set():
                self._emit("error", message=f"Incoming connection: {exc}")
        finally:
            self._disconnect(peer, sock)

    def connect(self, ip, port):
        if self._server is None or self._stopping.is_set():
            raise RuntimeError("start your peer first")
        ip = validate_ip(ip)
        port = validate_port(port)
        if port == self.port and ip in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("cannot connect to your own listening port")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        peer = None
        try:
            sock.settimeout(10)
            sock.connect((ip, port))
            send_message(sock, self._hello("hello"))
            identity = self._identify(receive_message(sock), "hello_ack")
            sock.settimeout(None)
            peer = self._register(sock, ip, identity)
            threading.Thread(
                target=self._outgoing_reader,
                args=(peer,),
                name="peer-reader",
                daemon=True,
            ).start()
            return peer.peer_id
        except Exception:
            if peer is None:
                self._close_socket(sock)
            raise

    def _outgoing_reader(self, peer):
        try:
            self._read_loop(peer)
        except (OSError, EOFError, ProtocolError, ValueError) as exc:
            if not self._stopping.is_set():
                self._emit("error", message=f"Connection to {peer.name}: {exc}")
        finally:
            self._disconnect(peer, peer.sock)

    def _read_loop(self, peer):
        while not self._stopping.is_set():
            message = receive_message(peer.sock)
            kind = message["type"]
            if kind == "text":
                body = message.get("message")
                if not isinstance(body, str) or not body or len(body) > 20_000:
                    raise ProtocolError("invalid text message")
                self._emit("text", peer_id=peer.peer_id, name=peer.name, message=body)
            elif kind == "file":
                self._receive_file(peer, message)
            else:
                raise ProtocolError("unexpected message type: " + kind)

    def _receive_file(self, peer, message):
        filename = _clean_filename(message.get("filename"))
        size = message.get("filesize")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_FILE_BYTES:
            raise ProtocolError("invalid file size")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        stem, suffix = os.path.splitext(filename)
        index = 0
        while True:
            candidate = filename if index == 0 else f"{stem} ({index}){suffix}"
            target = self.downloads_dir / candidate
            try:
                stream = target.open("xb")
                break
            except FileExistsError:
                index += 1
        try:
            with stream:
                remaining = size
                while remaining:
                    chunk = peer.sock.recv(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise EOFError("peer disconnected during file transfer")
                    stream.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self._emit("file_received", peer_id=peer.peer_id, name=peer.name,
                   filename=candidate, size=size, path=str(target))

    def _get_peer(self, peer_id):
        with self._lock:
            peer = self._peers.get(peer_id)
        if peer is None:
            raise ValueError("select a connected peer")
        return peer

    def peers(self):
        with self._lock:
            return [(p.peer_id, p.name, p.address, p.port) for p in self._peers.values()]

    def send_text(self, peer_id, message):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("enter a message first")
        if len(message) > 20_000:
            raise ValueError("message is too long")
        peer = self._get_peer(peer_id)
        with peer.send_lock:
            send_message(peer.sock, {"type": "text", "sender_id": self.peer_id,
                                     "sender_name": self.name, "message": message})
        self._emit("text_sent", peer_id=peer_id, name=peer.name, message=message)

    def send_file(self, peer_id, path):
        peer = self._get_peer(peer_id)
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError("file does not exist")
        size = source.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError("file is too large")
        with peer.send_lock:
            metadata_sent = False
            try:
                with source.open("rb") as stream:
                    send_message(peer.sock, {"type": "file", "sender_id": self.peer_id,
                                             "sender_name": self.name,
                                             "filename": source.name, "filesize": size})
                    metadata_sent = True
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            raise OSError("source file changed during transfer")
                        peer.sock.sendall(chunk)
                        remaining -= len(chunk)
            except Exception:
                # After metadata was sent, any missing bytes would corrupt framing.
                if metadata_sent:
                    self._disconnect(peer, peer.sock)
                raise
        self._emit("file_sent", peer_id=peer_id, name=peer.name,
                   filename=source.name, size=size)

    def _disconnect(self, peer, sock):
        removed = False
        if peer is not None:
            with self._lock:
                if self._peers.get(peer.peer_id) is peer:
                    del self._peers[peer.peer_id]
                    removed = True
        self._close_socket(sock)
        if removed and not self._stopping.is_set():
            self._emit("disconnected", peer_id=peer.peer_id, name=peer.name)
