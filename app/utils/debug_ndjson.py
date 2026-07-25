"""Session debug NDJSON logger (debug mode). Safe no-op if path unwritable."""

from __future__ import annotations

import json
import time
from pathlib import Path

_SESSION = "5182ef"
_PATHS = (
    Path("/home/arman/Desktop/3x-ui-telgram-bot/.cursor/debug-5182ef.log"),
    Path("/app/data/debug-5182ef.log"),
)


def agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
    *,
    run_id: str = "post-fix",
) -> None:
    # #region agent log
    payload = {
        "sessionId": _SESSION,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    for path in _PATHS:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
            break
        except OSError:
            continue
    # #endregion
