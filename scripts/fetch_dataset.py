#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.
"""Download a real dataset for WAU data-exchange efficiency testing.

The dataset itself is intentionally *not* committed (see `.gitignore`); this
script fetches it on demand into a git-ignored `datasets/` directory, skipping
files that already exist. It is used to feed real operand streams through the
WAU mesh (e.g. the DE0-Nano CW stress benchmark) instead of only random data,
so host<->device and inter-core data-exchange throughput can be measured on
something representative.

Currently supports MNIST (classic 28x28 grayscale digits), pulled from the
reliable CVDF mirror. The four idx.gz files together are ~11 MB.

Usage:
    python scripts/fetch_dataset.py                 # fetch MNIST if missing
    python scripts/fetch_dataset.py --dry-run       # show what would happen
    python scripts/fetch_dataset.py --force         # re-download everything
    python scripts/fetch_dataset.py --dest some/dir # custom destination

The reader helpers (`load_mnist_images`, `load_mnist_labels`) are importable so
other tooling can stream the pixels without pulling in numpy.
"""
from __future__ import annotations

import argparse
import gzip
import struct
import sys
from pathlib import Path
from urllib.request import urlopen

# CVDF mirror is stable and permissively hosted (the canonical yann.lecun.com
# host is frequently offline). Files are the original MNIST idx.gz encoding.
MNIST_MIRROR = "https://storage.googleapis.com/cvdf-datasets/mnist/"

# name -> (expected idx magic number, expected item count) for post-download
# verification. Magic 2051 = images (idx3), 2049 = labels (idx1).
MNIST_FILES: dict[str, tuple[int, int]] = {
    "train-images-idx3-ubyte.gz": (2051, 60000),
    "train-labels-idx1-ubyte.gz": (2049, 60000),
    "t10k-images-idx3-ubyte.gz": (2051, 10000),
    "t10k-labels-idx1-ubyte.gz": (2049, 10000),
}

DATASETS = ("mnist",)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_dest(dataset: str) -> Path:
    return repo_root() / "datasets" / dataset


def load_mnist_images(path: Path) -> tuple[int, int, int, bytes]:
    """Read an MNIST images idx3 file (plain or .gz).

    Returns (count, rows, cols, pixels) where `pixels` is the raw uint8 image
    data (count*rows*cols bytes). No numpy dependency.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        magic, count, rows, cols = struct.unpack(">IIII", handle.read(16))
        if magic != 2051:
            raise ValueError(f"{path}: bad images magic {magic} (expected 2051)")
        pixels = handle.read(count * rows * cols)
    if len(pixels) != count * rows * cols:
        raise ValueError(f"{path}: truncated image data")
    return count, rows, cols, pixels


def load_mnist_labels(path: Path) -> tuple[int, bytes]:
    """Read an MNIST labels idx1 file (plain or .gz). Returns (count, labels)."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        magic, count = struct.unpack(">II", handle.read(8))
        if magic != 2049:
            raise ValueError(f"{path}: bad labels magic {magic} (expected 2049)")
        labels = handle.read(count)
    if len(labels) != count:
        raise ValueError(f"{path}: truncated label data")
    return count, labels


def _verify(path: Path, expected_magic: int, expected_count: int) -> None:
    with gzip.open(path, "rb") as handle:
        header = handle.read(8)
    magic, count = struct.unpack(">II", header)
    if magic != expected_magic:
        raise ValueError(f"{path.name}: magic {magic} != expected {expected_magic}")
    if count != expected_count:
        raise ValueError(f"{path.name}: count {count} != expected {expected_count}")


def _download(url: str, dest: Path, *, quiet: bool) -> None:
    if not quiet:
        print(f"  downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urlopen(url) as response, tmp.open("wb") as out:  # noqa: S310 (trusted mirror)
        total = 0
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    tmp.replace(dest)
    if not quiet:
        print(f"    wrote {dest.relative_to(repo_root())} ({total} bytes)")


def fetch_mnist(dest: Path, *, force: bool, dry_run: bool, quiet: bool) -> int:
    print(f"[fetch_dataset] MNIST -> {dest}")
    fetched = 0
    for name, (magic, count) in MNIST_FILES.items():
        target = dest / name
        if target.exists() and not force:
            if not quiet:
                print(f"  present: {name} (skip; use --force to re-download)")
            _verify(target, magic, count)
            continue
        if dry_run:
            print(f"  would fetch: {MNIST_MIRROR + name}")
            fetched += 1
            continue
        _download(MNIST_MIRROR + name, target, quiet=quiet)
        _verify(target, magic, count)
        fetched += 1

    if dry_run:
        print(f"[fetch_dataset] dry-run: {fetched} file(s) would be fetched")
    else:
        print(f"[fetch_dataset] ok: {fetched} file(s) fetched, all verified in {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=DATASETS, default="mnist", help="dataset to fetch")
    parser.add_argument("--dest", type=Path, default=None, help="destination dir (default: datasets/<dataset>)")
    parser.add_argument("--force", action="store_true", help="re-download even if files exist")
    parser.add_argument("--dry-run", action="store_true", help="print planned downloads without fetching")
    parser.add_argument("--quiet", action="store_true", help="reduce per-file output")
    args = parser.parse_args(argv)

    dest = args.dest if args.dest is not None else default_dest(args.dataset)
    try:
        if args.dataset == "mnist":
            return fetch_mnist(dest, force=args.force, dry_run=args.dry_run, quiet=args.quiet)
    except Exception as exc:  # noqa: BLE001
        print(f"[fetch_dataset] error: {exc}", file=sys.stderr)
        return 1

    print(f"[fetch_dataset] unsupported dataset: {args.dataset}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
