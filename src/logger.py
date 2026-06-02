from pathlib import Path
from datetime import datetime

LOG_DIR = Path("data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

def log_event(event: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file = LOG_DIR / "usage_log.txt"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {event}\n")
