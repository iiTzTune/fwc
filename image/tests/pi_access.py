"""Provision and exercise wired access on a disposable Pi image."""

import http.client
import json
import socket
import subprocess
import time


def provision(disk, work, start, sectors):
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", work / "owner"],
        check=True,
    )
    data = work / "data.ext4"
    # Extract only FWC-DATA. The release disk and both root slots stay pristine.
    with disk.open("r+b") as image:
        image.seek(start * 512)
        data.write_bytes(image.read(sectors * 512))
        subprocess.run(
            ["debugfs", "-w", "-R", "write owner.pub /ssh/authorized_keys", data],
            cwd=work,
            check=True,
        )
        image.seek(start * 512)
        image.write(data.read_bytes())


def ports():
    with socket.socket() as http, socket.socket() as ssh:
        http.bind(("127.0.0.1", 0))
        ssh.bind(("127.0.0.1", 0))
        return http.getsockname()[1], ssh.getsockname()[1]


def check(work, forwards, identity):
    http_port, ssh_port = forwards
    # Pin the first boot's host key under a stable alias across forwarded ports.
    command = [
        "ssh",
        "-F",
        "/dev/null",
        "-p",
        str(ssh_port),
        "-i",
        str(work / "owner"),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=3",
        "-o",
        "HostKeyAlias=fwc-guest",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={work / 'known_hosts'}",
        "root@127.0.0.1",
        "cat /var/lib/fwc/device-id; ip -4 -o addr show dev eth0",
    ]
    for _ in range(30):
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"Guest SSH failed: {result.stderr}")
    lines = result.stdout.splitlines()
    assert lines[0] == identity
    address = lines[1].split(" inet ", 1)[1].split("/", 1)[0]
    conn = http.client.HTTPConnection("127.0.0.1", http_port, timeout=5)
    try:
        conn.request("GET", "/api/status", headers={"Host": f"{address}:8080"})
        response = conn.getresponse()
        assert response.status == 200
        services = json.load(response)["services"]
        assert {"wired", "ssh"} <= {s["name"] for s in services if s["pid"]}
    finally:
        conn.close()
    print(
        f"Guest DHCP {address}, key-only SSH login and wired dashboard verified.",
        flush=True,
    )
