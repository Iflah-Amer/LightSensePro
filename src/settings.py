from dataclasses import dataclass, asdict
import json
from pathlib import Path


@dataclass
class AppSettings:
    # --------- Visual Modes (already working) ----------
    adaptive_on: bool = True
    filter_on: bool = False
    sharpness_on: bool = False

    # sliders (visual)
    brightness: float = 1.00     # 1.0 = normal
    contrast: float = 1.20       # mild safe
    sharpness: float = 0.80      # 0..~1.2

    # --------- Reading Mode ----------
    reading_on: bool = False
    zoom: float = 2.0                 # 1.0..4.0
    reading_sharpness: float = 1.10   # 0.8..1.8
    reading_edges: float = 0.70       # 0..1.0 (edge intensity)
    reading_edge_thickness: int = 2   # 1..5

    # --------- Emergency ----------
    emergency_on: bool = False


def save_profile(settings: AppSettings, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, indent=2)


def load_profile(path: str) -> AppSettings:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Backward compatible defaults if old profiles miss new keys
    defaults = asdict(AppSettings())
    defaults.update(data)
    return AppSettings(**defaults)
