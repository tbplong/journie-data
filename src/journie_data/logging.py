from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RunLogger:
    def __init__(self, run_id: str, root: Path | str = "logs") -> None:
        self.run_id = run_id
        self.root = Path(root) / run_id

    def event(self, component: str, event: str, **fields: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "component": component,
            "event": event,
            **fields,
        }
        with (self.root / f"{component}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
