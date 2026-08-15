from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StructuredLogger:
    def __init__(self, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"runner-{datetime.now(timezone.utc):%Y%m%d}.jsonl"
        self.console = logging.getLogger("fpl-symphony")
        if not self.console.handlers:
            handler = logging.StreamHandler(); handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.console.addHandler(handler); self.console.setLevel(logging.INFO)

    def event(self, name: str, **fields: Any) -> None:
        def redact(value: Any) -> Any:
            if not isinstance(value, str): return value
            value = re.sub(r"(?:github_pat_|gh[opurs]_)[A-Za-z0-9_]+", "[REDACTED]", value)
            return re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)
        safe = {key: redact(value) for key, value in fields.items() if "token" not in key.lower() and "secret" not in key.lower()}
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": name, **safe}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, default=str) + "\n")
        self.console.info("%s %s", name, " ".join(f"{k}={v}" for k, v in safe.items()))
