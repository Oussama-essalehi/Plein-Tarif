"""Pipeline entry point (cross-platform equivalent of the Makefile).

Usage:
    python run.py <target>   # download | parse | clean | enrich | model | analyze | maps | export | test | all
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Each target runs the `main()` function of the listed modules, in order.
TARGETS: dict[str, list[str]] = {
    "download": ["plein_tarif.download"],
    "parse": ["plein_tarif.parse_xml"],
    "clean": ["plein_tarif.clean", "plein_tarif.daily_panel"],
    "enrich": ["plein_tarif.market", "plein_tarif.enrich_brands"],
    "model": ["plein_tarif.model"],
    "analyze": ["plein_tarif.analyze", "plein_tarif.rockets_feathers"],
    "maps": ["plein_tarif.maps"],
    "export": ["plein_tarif.export_gold"],
}
ALL_ORDER = ["download", "parse", "clean", "enrich", "model", "analyze", "maps", "export"]


def run_target(name: str) -> None:
    """Run every module of one target."""
    for module_name in TARGETS[name]:
        start = time.perf_counter()
        print(f"==> {name}: {module_name}", flush=True)
        importlib.import_module(module_name).main()
        print(f"<== {module_name} done in {time.perf_counter() - start:.1f}s", flush=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in [*TARGETS, "test", "all"]:
        print(__doc__)
        return 2
    target = argv[1]
    if target == "test":
        return subprocess.call([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")])
    for name in ALL_ORDER if target == "all" else [target]:
        run_target(name)
    if target == "all":
        return subprocess.call([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
