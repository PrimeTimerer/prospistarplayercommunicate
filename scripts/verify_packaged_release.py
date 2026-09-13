"""Read-only PyInstaller asset/module audit with create-only artifact reports."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.utils.win32.versioninfo import read_version_info_from_executable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product_version import APP_VERSION, WINDOWS_VERSION


def _fixed_version(ms: int, ls: int) -> tuple[int, int, int, int]:
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def _version_resource(artifact: Path) -> dict:
    info = read_version_info_from_executable(str(artifact))
    if info is None:
        return {"ok": False, "expected": APP_VERSION, "error": "missing version resource"}
    strings = {}
    for child in getattr(info, "kids", ()):
        for table in getattr(child, "kids", ()):
            for entry in getattr(table, "kids", ()):
                name = getattr(entry, "name", None)
                if isinstance(name, str):
                    strings[name] = getattr(entry, "val", None)
    file_version = _fixed_version(info.ffi.fileVersionMS, info.ffi.fileVersionLS)
    product_version = _fixed_version(info.ffi.productVersionMS, info.ffi.productVersionLS)
    ok = (
        file_version == WINDOWS_VERSION
        and product_version == WINDOWS_VERSION
        and strings.get("FileVersion") == APP_VERSION
        and strings.get("ProductVersion") == APP_VERSION
    )
    return {
        "ok": ok,
        "expected": APP_VERSION,
        "file_version": list(file_version),
        "product_version": list(product_version),
        "file_version_text": strings.get("FileVersion"),
        "product_version_text": strings.get("ProductVersion"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    artifact, target = Path(args.artifact).resolve(), Path(args.report).resolve()
    artifact.relative_to(ROOT)
    target.relative_to(ROOT / "artifacts")
    if target.exists() or not target.parent.is_dir():
        raise ValueError("Use a new report inside an existing artifact directory.")
    archive = CArchiveReader(str(artifact))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    files = [ROOT / "data/npb_records.json", ROOT / "tools/winrt_ocr.ps1"]
    for directory in ("ui", "data/packs", "data/editorial", "data/interactions", "data/parser_profiles", "data/story"):
        files.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    assets = []
    for path in sorted(files):
        name = path.relative_to(ROOT).as_posix()
        source = hashlib.sha256(path.read_bytes()).hexdigest()
        packaged = hashlib.sha256(archive.extract(names[name])).hexdigest() if name in names else None
        assets.append({"path": name, "source_sha256": source, "packaged_sha256": packaged, "ok": source == packaged})
    pyz_name = next(name for name in archive.toc if name.lower().endswith(".pyz"))
    pyz = archive.open_embedded_archive(pyz_name)
    required = ("interaction_intent", "interaction_voice", "star_interactions", "world_identity", "personal_context",
                "backend_check", "editorial_engine", "service", "app_shell", "ui_server", "save_reader", "ledger_v2",
                "gemini_provider", "secret_store", "provider_usage", "provider_expression", "provider_feed", "community_style_db", "local_model_manager", "prose_format", "narrative_context", "narrative_review", "product_version", "memory_windows", "story_desk", "story_desk_service", "story_links", "chronicle", "chronicle_service")
    modules = {name: name in pyz.toc for name in required}
    version = _version_resource(artifact)
    report = {"artifact": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
              "bytes": artifact.stat().st_size, "assets": assets, "modules": modules, "version": version,
              "ok": all(row["ok"] for row in assets) and all(modules.values()) and version["ok"],
              "limits": ["Asset byte equality and module presence, not native-window or live-save verification."]}
    with target.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({key: report[key] for key in ("ok", "sha256", "bytes")} |
                     {"matching_assets": sum(row["ok"] for row in assets), "required_modules": modules,
                      "version": version}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
