#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

SPDX_IDENTIFIER = "PolyForm-Noncommercial-1.0.0"
LICENSE_REF_TEXT = "See LICENSE at the repository root."

PY_HEADER_LINES = (
    f"# SPDX-License-Identifier: {SPDX_IDENTIFIER}",
    f"# {LICENSE_REF_TEXT}",
)

VERILOG_HEADER_LINES = (
    f"// SPDX-License-Identifier: {SPDX_IDENTIFIER}",
    f"// {LICENSE_REF_TEXT}",
)

HEADER_BY_SUFFIX: dict[str, tuple[str, ...]] = {
    ".py": PY_HEADER_LINES,
    ".v": VERILOG_HEADER_LINES,
    ".vh": VERILOG_HEADER_LINES,
}


def _iter_source_files(src_root: Path) -> Iterable[Path]:
    for path in sorted(src_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in HEADER_BY_SUFFIX:
            yield path


def _strip_existing_header(text: str, comment_prefix: str) -> str:
    lines = text.splitlines(keepends=True)
    index = 0

    if comment_prefix == "#" and lines and lines[0].startswith("#!"):
        index = 1

    scan = index
    while scan < len(lines):
        stripped = lines[scan].strip()
        if not stripped:
            scan += 1
            continue
        if stripped.startswith(comment_prefix):
            scan += 1
            continue
        break

    header_block = "".join(lines[index:scan])
    has_spdx = "SPDX-License-Identifier:" in header_block
    has_repo_ref = LICENSE_REF_TEXT in header_block
    if has_spdx or has_repo_ref:
        return "".join(lines[:index] + lines[scan:])
    return text


def _apply_header(text: str, header_lines: tuple[str, ...], comment_prefix: str) -> str:
    normalized = _strip_existing_header(text, comment_prefix)
    lines = normalized.splitlines(keepends=True)
    insert_index = 1 if lines and lines[0].startswith("#!") else 0

    header = "\n".join(header_lines) + "\n\n"
    if insert_index == 1:
        return "".join(lines[:1]) + header + "".join(lines[1:])
    return header + normalized


def _ensure_header(path: Path) -> bool:
    ext = path.suffix
    header_lines = HEADER_BY_SUFFIX[ext]
    comment_prefix = header_lines[0].split(" ", maxsplit=1)[0]

    original = path.read_text(encoding="utf-8")
    updated = _apply_header(original, header_lines, comment_prefix)
    if updated == original:
        return False

    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Add/update SPDX license headers in src source files.")
    parser.add_argument("--check", action="store_true", help="Report files that would change and exit non-zero.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"

    changed: list[Path] = []
    for file_path in _iter_source_files(src_root):
        if args.check:
            ext = file_path.suffix
            header_lines = HEADER_BY_SUFFIX[ext]
            comment_prefix = header_lines[0].split(" ", maxsplit=1)[0]
            original = file_path.read_text(encoding="utf-8")
            updated = _apply_header(original, header_lines, comment_prefix)
            if updated != original:
                changed.append(file_path)
            continue

        if _ensure_header(file_path):
            changed.append(file_path)

    if changed:
        action = "needs update" if args.check else "updated"
        for path in changed:
            print(f"{action}: {path.relative_to(repo_root)}")

    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
