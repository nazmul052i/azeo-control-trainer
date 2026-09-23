"""Small HTTP client usable by all products, without a PostgreSQL dependency."""
from __future__ import annotations

import json
from pathlib import Path
import ssl
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .documents import ConfigurationError, Conflict, Forbidden, Missing


class ServiceUnavailable(ConfigurationError):
    """Only an outage permits a previously authorized cached catalog fallback."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A credential entered for one service must never follow a redirect elsewhere.
        return None


class ConfigurationClient:
    def __init__(self, url: str, token: str, *, timeout=45):
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {"", "/"}):
            raise ConfigurationError("Enter the configuration service's HTTP(S) origin")
        if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ConfigurationError("Remote configuration connections require HTTPS")
        self.url, self._token = url.rstrip("/"), token.strip()
        self.timeout = timeout
        self._opener = None
        self._opener_lock = threading.Lock()

    def _transport(self):
        with self._opener_lock:
            if self._opener is None:
                # urllib eagerly loads the Windows certificate stores even for
                # HTTP. Build handlers once on the request worker, and only load
                # trust roots when this service actually uses HTTPS. Verification
                # remains mandatory on every HTTPS connection.
                context = (ssl.create_default_context() if self.url.startswith("https:") else
                           ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
                self._opener = build_opener(ProxyHandler({}), _NoRedirect(),
                                            HTTPSHandler(context=context))
            return self._opener

    def request(self, path: str, payload: dict | None = None):
        if not path.startswith("/v1/"):
            raise ConfigurationError("Unsupported configuration API path")
        request = Request(self.url + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": f"Bearer {self._token}",
                                   "Content-Type": "application/json"})
        try:
            # A local service must not accidentally send its token through a seat proxy.
            with self._transport().open(request, timeout=self.timeout) as response:
                result = response.read(190 * 1024 * 1024 + 1)
                if len(result) > 190 * 1024 * 1024:
                    raise ConfigurationError("Configuration service response is too large")
                return json.loads(result)
        except HTTPError as error:
            try:
                message = json.loads(error.read(8192)).get("detail", "Configuration request failed")
            except (ValueError, AttributeError):
                message = f"Configuration request failed ({error.code})"
            error_class = {401: Forbidden, 403: Forbidden, 404: Missing, 409: Conflict,
                           502: ServiceUnavailable, 503: ServiceUnavailable,
                           504: ServiceUnavailable}.get(error.code, ConfigurationError)
            raise error_class(str(message)) from None
        except (URLError, TimeoutError, OSError):
            raise ServiceUnavailable("Cannot reach the configuration service. Check its address "
                                     "and that it is running.") from None

    def upload_evidence(self, source, kind, identity):
        from .repository import identifier
        from .recovery import MAX_EVIDENCE
        source = Path(source)
        size = source.stat().st_size
        if kind not in {"history", "training", "journal", "backup"} or size > MAX_EVIDENCE:
            raise ConfigurationError("Choose a supported evidence archive no larger than 2 GB")
        path = f"/v1/recovery/evidence/{identifier(identity)}?kind={kind}"
        with source.open("rb") as stream:
            request = Request(self.url + path, data=stream, method="PUT", headers={
                "Authorization": f"Bearer {self._token}", "Content-Type": "application/octet-stream", "Content-Length": str(size)})
            try:
                with self._transport().open(request, timeout=self.timeout) as response:
                    return json.loads(response.read(1024 * 1024))
            except HTTPError as error:
                try:
                    message = json.loads(error.read(8192)).get("detail", "Archive upload failed")
                except (ValueError, AttributeError):
                    message = "Archive upload failed"
                raise ConfigurationError(str(message)) from None
            except (URLError, TimeoutError, OSError):
                raise ServiceUnavailable("Archive upload interrupted; the original local archive is unchanged") from None

    def download_backup(self, identity, destination):
        from .repository import identifier
        destination = Path(destination)
        request = Request(self.url + f"/v1/recovery/backups/{identifier(identity)}/download",
                          headers={"Authorization": f"Bearer {self._token}"})
        # An interrupted transfer must never look like a completed backup.
        # Link publishes atomically without overwriting an existing destination.
        from uuid import uuid4
        import os
        temporary = destination.with_name(destination.name + "." + uuid4().hex + ".partial")
        try:
            with self._transport().open(request, timeout=self.timeout) as response:
                with temporary.open("xb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


def default_profile() -> Path:
    from azeo_control_trainer.config.paths import data_dir
    return data_dir() / "configuration" / "client.json"


def read_profile(path: Path | None = None) -> dict:
    try:
        value = json.loads((path or default_profile()).read_text(encoding="utf-8"))
        return {"url": str(value.get("url", "")), "token": str(value.get("token", ""))}
    except (OSError, ValueError, AttributeError):
        return {"url": "http://127.0.0.1:8766", "token": ""}
