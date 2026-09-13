"""Audit the tracked publication tree without printing matched secret values."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DENIED_NAMES = {
    "config.json", "persona.txt", "secrets.json", "provider-usage.json",
    "ledger.json", "world-index.json", "HANDOFF.md", "REPORT.md", "SKILLS_HISTORY.md",
}
DENIED_DIRS = {"backups", "output", "shots", "artifacts", "logs", "profiles", "quarantine", ".git"}
PATTERNS = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "github_token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "openai_key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{35,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "shared_chat": re.compile(r"https?://(?:chatgpt\.com/share/|gemini\.google\.com/gem/)[A-Za-z0-9_-]{8,}"),
    "personal_windows_path": re.compile(r"[A-Za-z]:[/\\]+Users[/\\]+(?!Public\b|Default\b)[A-Za-z0-9_.-]+", re.I),
    "personal_unix_path": re.compile(r"/(?:Users|home)/[a-zA-Z][A-Za-z0-9_.-]+/"),
    "personal_email": re.compile(r"[A-Za-z0-9._%+-]+@(?:gmail|naver|hanmail|daum)\.(?:com|net)", re.I),
}


def path_allowed(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.name in DENIED_NAMES:
        return False
    if set(path.parts).intersection(DENIED_DIRS) or path.name.startswith(".env"):
        return False
    if path.suffix.lower() in {".dat", ".exe", ".dll", ".zip", ".log", ".db", ".sqlite", ".gguf", ".safetensors", ".pem", ".key"}:
        return False
    if path.suffix.lower() in {".bin", ".png", ".ico"}:
        return name in {"tests/fixtures/starplayer-plain.bin", "ui/assets/starmodefeed-mark.png", "assets/StarModeFeed.ico"}
    if path.parts[0] == "data":
        return path.suffix == ".json" and (name == "data/npb_records.json" or len(path.parts) > 2 and path.parts[1] in {"packs","editorial","interactions","parser_profiles","story"})
    return path.suffix.lower() in {".py", ".spec", ".ps1", ".js", ".cjs", ".css", ".html", ".json", ".md", ".txt", ".yml", ".yaml"} or path.name in {"LICENSE", ".gitignore", ".gitattributes"}


def text_findings(text):
    return [{"kind":kind,"line":text.count("\n",0,match.start())+1}
            for kind, pattern in PATTERNS.items() for match in pattern.finditer(text)]


def audit(root=ROOT):
    from scripts.build_test_fixture import payloads
    expected = payloads()
    names = subprocess.check_output(["git","ls-files","-z"],cwd=root).decode().split("\0")
    issues = []
    count = 0
    for name in filter(None,names):
        count += 1
        path = root / name
        if not path_allowed(name) or path.is_symlink():
            issues.append({"file":name,"kind":"forbidden_path"})
            continue
        data = path.read_bytes()
        if name.startswith("tests/fixtures/"):
            fixture_name = name.removeprefix("tests/fixtures/")
            if fixture_name in expected and data != expected[fixture_name]:
                issues.append({"file":name,"kind":"non_synthetic_fixture"})
        if path.suffix.lower() in {".bin", ".png", ".ico"}:
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            issues.append({"file":name,"kind":"unexpected_encoding"})
            continue
        issues.extend({"file":name,**finding} for finding in text_findings(text))
    for fixture_name, data in expected.items():
        name = "tests/fixtures/" + fixture_name
        if name not in names or not (root / name).is_file() or (root / name).read_bytes() != data:
            issues.append({"file":name,"kind":"missing_or_changed_synthetic_fixture"})
    return {"ok":count > 0 and not issues,"tracked_files":count,"issues":issues,
            "note":"Pattern checks plus deterministic fixtures; manual review is still required."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if str(ROOT) not in sys.path:
        sys.path.insert(0,str(ROOT))
    result = audit()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
