"""Distribution files cannot accidentally contain private server code or credentials."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.package import ROOT, build


def test_install_zip_allowlist_and_repeatability(tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    assert build(first) == 24
    build(second)
    assert first.read_bytes() == second.read_bytes()
    with ZipFile(first) as archive:
        names = archive.namelist()
        assert "custom_components/sscape/manifest.json" in names
        assert "custom_components/sscape/brand/icon.png" in names
        assert "LICENSE" in names
        assert all(
            name.startswith("custom_components/sscape/") or name in ("LICENSE", "README.md")
            for name in names
        )
        assert not any("__pycache__" in name or name.endswith((".env", ".pyc")) for name in names)
    with pytest.raises(FileExistsError):
        build(first)


def test_translations_and_manifests():
    component = ROOT / "custom_components/sscape"
    english = json.loads((component / "translations/en.json").read_text())
    italian = json.loads((component / "translations/it.json").read_text())
    assert english == json.loads((component / "strings.json").read_text())

    def keys(value, prefix=""):
        return {
            key
            for name, child in value.items()
            for key in (
                {f"{prefix}.{name}"}
                | (keys(child, f"{prefix}.{name}") if isinstance(child, dict) else set())
            )
        }

    assert keys(english) == keys(italian)
    manifest = json.loads((component / "manifest.json").read_text())
    assert manifest["domain"] == "sscape" and manifest["version"] == "0.3.0"
    assert manifest["config_flow"] and manifest["requirements"] == []
    assert manifest["categories"] == ["weather_alerts"]
    assert manifest["documentation"] == "https://github.com/leofabri/sscape-home-assistant#readme"
    assert manifest["issue_tracker"] == "https://github.com/leofabri/sscape-home-assistant/issues"
    assert Path(component / "__init__.py").is_file()
