"""Count recently observed clients by their latest receive channel."""

import sqlite3
import time
from collections import Counter
from contextlib import closing


def density_snapshot(directory, settings) -> dict[str, int]:
    window = settings.get("window_seconds", 60)
    if type(window) is not int or not 1 <= window <= 300:
        raise ValueError("window_seconds must be between 1 and 300")
    now = time.time_ns()
    uri = (directory / "state.sqlite").resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        rows = db.execute(
            """
            WITH recent AS (
                SELECT seq, data FROM events WHERE topic='association.event'
                ORDER BY seq DESC LIMIT 4096
            )
            SELECT json_extract(data,'$.network'), json_extract(data,'$.station'),
                   json_extract(data,'$.channel'), json_extract(data,'$.state')
            FROM recent
            WHERE json_type(data,'$.time_ns')='integer'
              AND json_extract(data,'$.time_ns') BETWEEN ? AND ?
              AND json_type(data,'$.channel')='integer'
              AND json_extract(data,'$.channel') BETWEEN 1 AND 233
              AND json_type(data,'$.network')='text'
              AND json_type(data,'$.station')='text'
              AND length(json_extract(data,'$.network'))=17
              AND length(json_extract(data,'$.station'))=17
            ORDER BY json_extract(data,'$.time_ns'), seq
            """,
            (now - window * 10**9, now),
        )
        locations = {}
        for network, client, channel, state in rows:
            if state == "joined":
                locations[client] = (network, channel)
            elif (
                state == "left"
                and client in locations
                and locations[client][0] == network
            ):
                del locations[client]
    return dict(Counter(str(channel) for _, channel in locations.values()))
