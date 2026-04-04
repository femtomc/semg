#!/usr/bin/env python3
"""Benchmark multiprocessing vs serial scan extraction."""
from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path

from smg.langs import REGISTRY, get_extractor, load_extractors
from smg.scan import file_to_module_name
from smg.storage import find_root


def extract_one(args: tuple) -> tuple | None:
    fpath_str, ext, rel, mod = args
    load_extractors()
    extractor = get_extractor(ext)
    if extractor is None:
        return None
    source = Path(fpath_str).read_bytes()
    result = extractor.extract(source, rel, mod)
    return (
        [(n.name, n.type.value, n.file, n.line, n.end_line, n.docstring, n.metadata) for n in result.nodes],
        [(e.source, e.target, e.rel.value, e.metadata) for e in result.edges],
    )


def main() -> None:
    load_extractors()
    root = find_root()
    all_files = sorted((root / "src").glob("**/*.py")) + sorted((root / "tests").glob("**/*.py"))

    file_infos = []
    for f in all_files:
        ext = f.suffix
        rel = str(f.relative_to(root))
        mod = file_to_module_name(rel, root)
        file_infos.append((str(f), ext, rel, mod))

    print(f"{len(file_infos)} files, {mp.cpu_count()} cores\n")

    # Serial
    t0 = time.perf_counter()
    for _ in range(3):
        for args in file_infos:
            extract_one(args)
    t1 = time.perf_counter()
    serial_ms = (t1 - t0) / 3 * 1000
    print(f"Serial:            {serial_ms:.1f} ms")

    # Threading (GIL-bound, but tree-sitter releases GIL during parse)
    from concurrent.futures import ThreadPoolExecutor
    for nw in (2, 4, 8):
        t0 = time.perf_counter()
        for _ in range(3):
            with ThreadPoolExecutor(max_workers=nw) as pool:
                list(pool.map(extract_one, file_infos))
        t1 = time.perf_counter()
        ms = (t1 - t0) / 3 * 1000
        print(f"Threads ({nw}):        {ms:.1f} ms  ({serial_ms / ms:.1f}x)")

    # Multiprocessing
    for nw in (2, 4, 8):
        t0 = time.perf_counter()
        for _ in range(3):
            with mp.Pool(nw) as pool:
                list(pool.map(extract_one, file_infos))
        t1 = time.perf_counter()
        ms = (t1 - t0) / 3 * 1000
        print(f"Processes ({nw}):      {ms:.1f} ms  ({serial_ms / ms:.1f}x)")


if __name__ == "__main__":
    main()
