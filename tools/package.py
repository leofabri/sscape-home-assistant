"""Create a deterministic manual-install ZIP from an explicit file allowlist."""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "actions.py",
    "__init__.py",
    "api.py",
    "availability.py",
    "assist.py",
    "binary_sensor.py",
    "config_flow.py",
    "const.py",
    "coordinator.py",
    "dashboard.py",
    "diagnostics.py",
    "entity.py",
    "episodes.py",
    "event.py",
    "forecast.py",
    "hazards.py",
    "hazard_entities.py",
    "local_sensors.py",
    "journey.py",
    "notifications.py",
    "presentation.py",
    "status.py",
    "manifest.json",
    "measurements.py",
    "outlook.py",
    "planning.py",
    "sensor.py",
    "services.yaml",
    "setup_flow.py",
    "setup_panel.py",
    "stream.py",
    "targets.py",
    "weather.py",
    "strings.json",
    "translations/en.json",
    "translations/it.json",
    "brand/icon.png",
    "www/setup.js",
    "www/setup-controller.js",
    "www/setup-preview.js",
    "www/setup-tests.js",
    "www/setup.css",
)


def build(output: Path) -> int:
    manifest = json.loads((ROOT / "custom_components/sscape/manifest.json").read_text())
    if manifest["domain"] != "sscape" or not manifest["config_flow"]:
        raise ValueError("Invalid integration manifest")
    paths = [f"custom_components/sscape/{name}" for name in FILES] + ["LICENSE", "README.md"]
    if output.exists():
        raise FileExistsError("Choose a new output filename; existing files are not overwritten")
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for relative in sorted(paths):
            source = ROOT / relative
            if source.is_symlink():
                raise ValueError("Symlinks are not allowed in the package")
            info = ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
    return len(paths)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(f"Packaged {build(args.output)} files into {args.output}")
