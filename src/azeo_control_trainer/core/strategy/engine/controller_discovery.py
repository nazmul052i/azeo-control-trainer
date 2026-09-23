"""Controller discovery on the engineering Control Network.

Each running controller answers a small UDP probe with identity metadata. The
probe carries no control data and grants no trust: it only gives Explorer a
candidate to add or commission. Persistent association still happens through
the project's controller node record and its unique hardware id.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass


log = logging.getLogger("strategy.controller_discovery")

DISCOVERY_PORT = 42_049
PROTOCOL = "azeo-controller-discovery-v1"


@dataclass(frozen=True)
class ControllerAdvertisement:
    """Identity returned by one controller discovery responder."""

    hardware_id: str
    name: str
    model: str = "PK100"
    serial: str = ""
    address: str = ""
    opcua_endpoint: str = ""
    state: str = "available"
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict, *, source_address: str = ""):
        hardware_id = str(raw.get("hardware_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not hardware_id or not name:
            raise ValueError("controller advertisement needs hardware_id and name")
        return cls(
            hardware_id=hardware_id,
            name=name,
            model=str(raw.get("model") or "PK100").upper(),
            serial=str(raw.get("serial") or ""),
            address=str(raw.get("address") or source_address),
            opcua_endpoint=str(raw.get("opcua_endpoint") or ""),
            state=str(raw.get("state") or "available"),
            description=str(raw.get("description") or ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class ControllerDiscoveryResponder:
    """Background UDP responder owned by a running controller process."""

    def __init__(self, advertisement: ControllerAdvertisement,
                 *, host: str = "0.0.0.0", port: int = DISCOVERY_PORT):
        self.advertisement = advertisement
        self.host = host
        self.port = int(port)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error = ""

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._ready.clear()
        self.error = ""
        self._thread = threading.Thread(
            target=self._serve, name="controller-discovery", daemon=True)
        self._thread.start()
        self._ready.wait(1.0)
        return self._socket is not None and not self.error

    def _serve(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            self.port = int(sock.getsockname()[1])
            sock.settimeout(0.15)
            self._socket = sock
            self._ready.set()
            while not self._stop.is_set():
                try:
                    payload, peer = sock.recvfrom(16_384)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    request = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if request.get("protocol") != PROTOCOL \
                        or request.get("message") != "discover":
                    continue
                response = {
                    "protocol": PROTOCOL,
                    "message": "controller",
                    "nonce": request.get("nonce", ""),
                    "controller": self.advertisement.to_dict(),
                }
                try:
                    sock.sendto(json.dumps(response).encode("utf-8"), peer)
                except OSError:
                    break
        except OSError as error:
            self.error = str(error)
            log.warning("Controller discovery responder unavailable: %s", error)
            self._ready.set()
        finally:
            self._socket = None
            try:
                sock.close()
            except OSError:
                pass
            self._ready.set()

    def stop(self) -> None:
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None


def discover_controllers(
        *, timeout: float = 0.65, port: int = DISCOVERY_PORT,
        targets: tuple[str, ...] = ("255.255.255.255", "127.0.0.1"),
        ) -> list[ControllerAdvertisement]:
    """Probe the local Control Network and return unique controllers."""
    nonce = uuid.uuid4().hex
    request = json.dumps({
        "protocol": PROTOCOL,
        "message": "discover",
        "nonce": nonce,
    }).encode("utf-8")
    found: dict[str, ControllerAdvertisement] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        for target in targets:
            try:
                sock.sendto(request, (target, int(port)))
            except OSError:
                continue
        deadline = time.monotonic() + max(0.05, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                payload, peer = sock.recvfrom(16_384)
            except socket.timeout:
                break
            except OSError:
                break
            try:
                response = json.loads(payload.decode("utf-8"))
                if response.get("protocol") != PROTOCOL \
                        or response.get("message") != "controller" \
                        or response.get("nonce") != nonce:
                    continue
                item = ControllerAdvertisement.from_dict(
                    response.get("controller") or {},
                    source_address=peer[0])
            except (UnicodeDecodeError, ValueError, TypeError):
                continue
            found[item.hardware_id] = item
    finally:
        sock.close()
    return sorted(found.values(), key=lambda item: (item.name, item.hardware_id))
