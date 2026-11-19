"""Send a signed channel summary to a configured HTTPS receiver."""

import hashlib
import hmac
import http.client
import json
import os
import stat
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from fwc.capture.contract import plugin_config, require


def encode(summary):
    if (
        not isinstance(summary, dict)
        or summary.keys() != {"version", "networks", "owned", "channels"}
        or type(summary["version"]) is not int
        or summary["version"] != 1
        or type(summary["networks"]) is not int
        or type(summary["owned"]) is not int
        or not 0 <= summary["owned"] <= summary["networks"] <= 16
    ):
        raise ValueError("invalid network summary")
    channels = summary["channels"]
    if not isinstance(channels, list) or len(channels) > 16:
        raise ValueError("invalid channel counts")
    total = previous = 0
    for item in channels:
        if (
            not isinstance(item, dict)
            or item.keys() != {"channel", "networks"}
            or type(item["channel"]) is not int
            or not previous < item["channel"] <= 233
            or type(item["networks"]) is not int
            or not 1 <= item["networks"] <= 16
        ):
            raise ValueError("invalid channel counts")
        previous = item["channel"]
        total += item["networks"]
    if total != summary["networks"]:
        raise ValueError("network and channel totals differ")
    return json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()


def destination_key(directory, endpoint):
    folder = directory / "export-keys"
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_mode & 0o077:
        raise ValueError("export-keys needs a private directory")
    path = folder / (hashlib.sha256(endpoint.encode()).hexdigest() + ".key")
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
            temporary = Path(temp.name)
            try:
                temp.write(os.urandom(32))
                temp.flush()
                os.fsync(temp.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass  # Another invocation already installed the complete key.
            finally:
                temporary.unlink(missing_ok=True)
    with os.fdopen(
        os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
    ) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("export key needs a private regular file")
        key = stream.read(33)
    if len(key) != 32:
        raise ValueError("export key must contain 32 bytes")
    return key


def upload(directory, summary):
    granted, settings = plugin_config(directory, "export-web")
    require(granted, {"networks.read", "network.out"})
    endpoint = settings.get("endpoint")
    if (
        set(settings) != {"endpoint"}
        or not isinstance(endpoint, str)
        or not 1 <= len(endpoint) <= 2048
        or not endpoint.isascii()
        or any(ord(c) <= 32 or ord(c) == 127 for c in endpoint)
    ):
        raise ValueError("configure an absolute HTTPS endpoint")
    url = urlsplit(endpoint)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.fragment
    ):
        raise ValueError("configure an HTTPS endpoint without credentials or fragment")
    body = encode(summary)
    signature = hmac.digest(destination_key(directory, endpoint), body, "sha256").hex()
    path = url.path or "/"
    if url.query:
        path += "?" + url.query
    with closing(
        http.client.HTTPSConnection(url.hostname, url.port, timeout=4)
    ) as conn:
        try:
            conn.request(
                "POST",
                path,
                body,
                {
                    "Content-Type": "application/json",
                    "X-FWC-Signature": "sha256=" + signature,
                },
            )
            with conn.getresponse() as response:
                if not 200 <= response.status < 300:
                    raise ValueError(f"Receiver returned HTTP {response.status}")
        except http.client.HTTPException as exc:
            raise ValueError("Invalid response from export receiver") from exc
    return f"Uploaded summary of {summary['networks']} network records"
