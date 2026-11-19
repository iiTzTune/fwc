"""Hand a single receive setting to the capture worker and await its result."""

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import closing

from fwc.capture.contract import PermissionDenied


def radio_snapshot(directory, plugin):
    with closing(
        sqlite3.connect((directory / "state.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        row = db.execute(
            "SELECT data FROM events WHERE topic='radio/state' ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        last = db.execute(
            """SELECT data FROM events WHERE topic='radio/result'
               AND json_extract(data,'$.plugin')=? AND json_extract(data,'$.ok')=1
               ORDER BY seq DESC LIMIT 1""",
            (plugin,),
        ).fetchone()
    return {
        "radio": json.loads(row[0]) if row else {},
        "last_radio": json.loads(last[0])["state"] if last else {},
    }


async def request(directory, plugin, command, publish):
    reader, writer = await asyncio.open_unix_connection(
        str(directory / "bus.sock"), limit=16384
    )
    request_id = uuid.uuid4().hex
    try:
        async with asyncio.timeout(65):
            writer.write(b'{"op":"subscribe","topic":"radio/result"}\n')
            await writer.drain()
            if not json.loads(await reader.readline())["ok"]:
                raise ValueError("radio result subscription unavailable")
            await publish(
                directory,
                {
                    "op": "publish",
                    "topic": "radio/command",
                    "id": request_id,
                    "data": {
                        "plugin": plugin,
                        "command": command,
                        "expires": time.monotonic() + 64,
                    },
                },
            )
            while True:
                line = await reader.readline()
                if not line:
                    raise OSError("radio result connection closed")
                result = json.loads(line)["data"]
                if (
                    result.get("request_id") == request_id
                    and result.get("plugin") == plugin
                ):
                    if result["ok"]:
                        return result["state"]
                    error = result["error"]
                    if error["code"] == "permission_denied":
                        raise PermissionDenied(error["permission"])
                    raise ValueError(error.get("detail", error["code"]))
    finally:
        writer.close()
        await writer.wait_closed()
