"""Assemble a reproducible x86-64 companion device image without host mounts."""

import concurrent.futures
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "image"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fwc-image"


def validate(config):
    expected = {"board", "architecture", "slot_mib", "data_mib", "epoch", "files"}
    if set(config) != expected or config["architecture"] != "x86_64":
        raise ValueError("Unknown board fields or architecture")
    for key, minimum in (("slot_mib", 384), ("data_mib", 32), ("epoch", 315532800)):
        if type(config[key]) is not int or not minimum <= config[key]:
            raise ValueError(f"Invalid board setting {key}")
    for item in config["files"]:
        source, dest = Path(item["source"]), Path(item["destination"])
        if (
            source.is_absolute()
            or not dest.is_absolute()
            or ".." in source.parts + dest.parts
        ):
            raise ValueError("Invalid device file mapping")
    return config


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(entry):
    digest, group, url = entry
    directory = CACHE / "inputs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / unquote(Path(urlparse(url).path).name)
    if not path.exists() or checksum(path) != digest:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temp:
            try:
                with urlopen(url, timeout=120) as stream:
                    shutil.copyfileobj(stream, temp)
                temp.flush()
                if checksum(Path(temp.name)) != digest:
                    raise ValueError(f"Checksum mismatch for {url}")
                os.replace(temp.name, path)
            finally:
                Path(temp.name).unlink(missing_ok=True)
    return group, path


def unpack(path, dest):
    args = [
        "tar",
        "--warning=no-unknown-keyword",
        "--ignore-zeros",
        "--no-same-owner",
        "--same-permissions",
        "-xzf",
        path,
        "-C",
        dest,
    ]
    if path.suffix == ".apk":
        args += ["--exclude=.[A-Za-z]*"]
    subprocess.run(args, check=True)


def alpine_roots(work):
    entries = [
        line.split() for line in (IMAGE / "alpine.lock").read_text().splitlines()
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        packages = list(pool.map(fetch, entries))
    runtime = work / "root"
    tools = work / "tools"
    runtime.mkdir()
    tools.mkdir()
    for group, archive in sorted(
        packages, key=lambda item: (item[0] != "base", item[1].name)
    ):
        if group != "tools":
            unpack(archive, runtime)
        if not archive.name.startswith(("linux-lts-", "linux-firmware-")):
            unpack(archive, tools)
    return runtime, tools


def run_inside(tools, work, command, *, read_source=True, fixed_time=False):
    args = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--uid",
        "0",
        "--gid",
        "0",
        "--bind",
        tools,
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--bind",
        work,
        "/work",
    ]
    if read_source:
        args += ["--ro-bind", ROOT, "/src", "--chdir", "/src"]
    env = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/tmp",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": str(work.joinpath("epoch").read_text().strip()),
        "CARGO_HOME": "/work/cargo",
        "CARGO_TARGET_DIR": "/work/target",
        "CARGO_INCREMENTAL": "0",
    }
    if fixed_time:
        env.update(
            {
                "LD_PRELOAD": "/usr/lib/faketime/libfaketime.so.1",
                "FAKETIME": datetime.datetime.fromtimestamp(
                    int(env["SOURCE_DATE_EPOCH"]), datetime.UTC
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "FAKETIME_DONT_FAKE_MONOTONIC": "1",
                "E2FSPROGS_FAKE_TIME": env["SOURCE_DATE_EPOCH"],
            }
        )
    for key, value in env.items():
        args += ["--setenv", key, value]
    subprocess.run([*args, *command], check=True, env={"PATH": os.environ["PATH"]})


def install_software(runtime, tools, work, config):
    cargo_home = work / "cargo"
    cargo_home.mkdir()
    with open(os.devnull, "w") as sink:
        subprocess.run(
            ["cargo", "vendor", "--locked", "--quiet", work / "vendor"],
            cwd=ROOT,
            stdout=sink,
            check=True,
        )
    (cargo_home / "config.toml").write_text(
        '[source.crates-io]\nreplace-with="local"\n[source.local]\ndirectory="/work/vendor"\n'
    )
    run_inside(
        tools,
        work,
        ["cargo", "build", "--release", "--locked", "--offline", "-p", "fwc-core"],
    )
    for binary in ["fwc-core", "fwc-update"]:
        shutil.copy2(work / "target/release" / binary, runtime / "usr/bin" / binary)
    wheels = work / "wheels"
    subprocess.run(
        [
            "uv",
            "build",
            "--all-packages",
            "--wheel",
            "--out-dir",
            wheels,
            "--build-constraint",
            IMAGE / "python-build.txt",
        ],
        cwd=ROOT,
        check=True,
    )
    site = runtime / "usr/lib/python3.12/site-packages"
    for wheel in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)
    for item in config["files"]:
        dest = runtime / item["destination"].lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / item["source"], dest)
    examples = ROOT / "plugins/examples"
    if examples.is_dir():
        shutil.copytree(
            examples,
            runtime / "usr/share/fwc/examples",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    for path in ["boot/efi", "var/lib/fwc", "run", "var/log", "tmp"]:
        (runtime / path).mkdir(parents=True, exist_ok=True)
    kernel = runtime / "boot/vmlinuz-lts"
    kernel.rename(runtime / "boot/vmlinuz")
    run_inside(tools, work, ["python3", "/src/image/pack.py", "initrd"])


def populate_data(work):
    data = work / "data"
    data.mkdir()
    services = [
        {
            "name": "web",
            "command": [
                "/usr/bin/python3",
                "/usr/share/fwc/web/server.py",
                "/var/lib/fwc",
                "/var/lib/fwc/capture.json",
            ],
            "restarts": 3,
            "window_secs": 60,
        }
    ]
    (data / "plugins").mkdir()
    if (ROOT / "plugins/src/fwc/plugins/__main__.py").exists():
        services.append(
            {
                "name": "plugins",
                "command": [
                    "/usr/bin/python3",
                    "-m",
                    "fwc.plugins",
                    "/var/lib/fwc/plugins",
                    "/var/lib/fwc",
                ],
                "restarts": 3,
                "window_secs": 60,
            }
        )
    (data / "core.json").write_text(json.dumps({"services": services}, indent=2) + "\n")
    (data / "capture.json").write_text(
        json.dumps(
            {
                "phy": "phy0",
                "interface": "fwcmon0",
                "directory": "/var/lib/fwc/captures",
            },
            indent=2,
        )
        + "\n"
    )
    capture = {
        "name": "capture",
        "command": [
            "/usr/bin/python3",
            "-m",
            "fwc.capture",
            "/var/lib/fwc/capture.json",
            "/var/lib/fwc/bus.sock",
        ],
        "restarts": 3,
        "window_secs": 60,
    }
    (data / "capture-service.json").write_text(json.dumps(capture, indent=2) + "\n")


def main():
    config = validate(json.loads((IMAGE / "device.json").read_text()))
    output = ROOT / "build/image"
    output.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    # Serialize publication of the disk/slot pair in this checkout.
    with (output / ".build.lock").open("a") as lock, contextlib.ExitStack() as stack:
        fcntl.flock(lock, fcntl.LOCK_EX)
        work = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=CACHE)))
        (work / "epoch").write_text(str(config["epoch"]))
        runtime, tools = alpine_roots(work)
        install_software(runtime, tools, work, config)
        populate_data(work)
        run_inside(
            tools, work, ["python3", "/src/image/pack.py", "disk"], fixed_time=True
        )
        for name in ("fwc-slot.ext4", "fwc-x86_64.img", "SHA256SUMS"):
            shutil.move(work / name, output / name)
        print((output / "SHA256SUMS").read_text(), end="")


if __name__ == "__main__":
    main()
