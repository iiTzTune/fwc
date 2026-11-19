"""Run one observation handler with a private filesystem and bounded output."""

import asyncio
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


async def invoke(package, snapshot):
    command = [
        "bwrap",
        "--unshare-all",
        "--unshare-user",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
    ]
    for name in ("/lib", "/lib64", "/usr/lib", "/usr/lib64"):
        path = Path(name)
        if path.is_dir():
            command += ["--ro-bind", str(path.resolve()), name]
    command += [
        "--ro-bind",
        "/usr/bin/python3",
        "/usr/bin/python3",
        "--ro-bind",
        str(HERE / "runner.py"),
        "/sdk/runner.py",
        "--ro-bind",
        str(HERE / "sdk.py"),
        "/sdk/fwc_sdk.py",
    ]
    # Snapshot the validated script. The plugin directory itself stays outside.
    with (
        tempfile.TemporaryFile() as script,
        tempfile.TemporaryFile() as out,
        tempfile.TemporaryFile() as errors,
    ):
        script.write(package.script)
        script.seek(0)
        command += [
            "--ro-bind-data",
            str(script.fileno()),
            "/work/main.py",
            "--remount-ro",
            "/",
            "--",
            "/usr/bin/python3",
            "-ISB",
            "/sdk/runner.py",
            str(package.memory_mib),
            str(package.cpu_seconds),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=out,
            stderr=errors,
            pass_fds=(script.fileno(),),
        )
        try:
            await asyncio.wait_for(
                process.communicate(json.dumps(snapshot).encode()), package.seconds
            )
        except TimeoutError as exc:
            raise ValueError("plugin exceeded its time limit") from exc
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode:
            errors.seek(0)
            detail = errors.read(8192).decode(errors="replace").strip().splitlines()
            raise ValueError(
                detail[-1][:160] if detail else f"process exited {process.returncode}"
            )
        out.seek(0)
        return json.loads(out.read(8192))
