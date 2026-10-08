import platform
import shutil
import sys
import threading
import time

import psutil

from config import DATA_DIR

_START = time.time()
_proc = psutil.Process()
_cpu = {"system": 0.0, "process": 0.0}


def _sample_cpu() -> None:
    # Sampled on a fixed cadence: psutil's interval=None readings are relative to the
    # previous call, so concurrent dashboard polls would otherwise read near-zero values.
    _proc.cpu_percent(None)
    while True:
        _cpu["system"] = psutil.cpu_percent(interval=2.0)
        _cpu["process"] = _proc.cpu_percent(None) / max(1, psutil.cpu_count() or 1)


threading.Thread(target=_sample_cpu, daemon=True, name="cpu-sampler").start()


def system_stats() -> dict:
    mem = psutil.virtual_memory()
    disk = shutil.disk_usage(DATA_DIR)
    return {
        "cpu_percent": _cpu["system"],
        "cpu_count": psutil.cpu_count(),
        "process_cpu_percent": round(_cpu["process"], 1),
        "memory_percent": mem.percent,
        "memory_used": mem.used,
        "memory_total": mem.total,
        "process_memory": _proc.memory_info().rss,
        "disk_free": disk.free,
        "disk_total": disk.total,
        "uptime_seconds": int(time.time() - _START),
        "platform": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "data_dir": str(DATA_DIR),
    }
