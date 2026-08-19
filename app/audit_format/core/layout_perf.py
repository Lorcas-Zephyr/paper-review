from typing import Dict, Any
import time


class PerfTimer:
    def __init__(self):
        self.records: Dict[str, int] = {}

    def measure(self, key: str, start: float, end: float):
        self.records[key] = int((end - start) * 1000)

    def total(self) -> int:
        return sum(self.records.values())


def timing_guard(fn, *args, **kwargs) -> Dict[str, Any]:
    start = time.time()
    result = fn(*args, **kwargs)
    end = time.time()
    return {"result": result, "latency_ms": int((end - start) * 1000)}
