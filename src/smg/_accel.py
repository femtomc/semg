"""Optional native acceleration via Zig shared library.

Loads libsmg_accel.so/.dylib if available. Falls back gracefully to None
if the library is not built or not found.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_lib = None


def _find_lib() -> ctypes.CDLL | None:
    """Try to load the native acceleration library."""
    # Look in native/zig-out/lib/ relative to the project root
    candidates = [
        Path(__file__).resolve().parents[2] / "native" / "zig-out" / "lib" / "libsmg_accel.so",
        Path(__file__).resolve().parents[2] / "native" / "zig-out" / "lib" / "libsmg_accel.dylib",
    ]
    for path in candidates:
        if path.exists():
            try:
                return ctypes.CDLL(str(path))
            except OSError:
                continue
    return None


def _load() -> ctypes.CDLL | None:
    global _lib
    if _lib is None:
        _lib = _find_lib()
        if _lib is not None:
            # Set up function signatures
            _lib.smg_betweenness.argtypes = [
                ctypes.c_uint32,                          # n
                ctypes.POINTER(ctypes.c_uint32),          # offsets
                ctypes.POINTER(ctypes.c_uint32),          # targets
                ctypes.POINTER(ctypes.c_double),          # out_bc
                ctypes.c_uint32,                          # max_sources
                ctypes.c_uint64,                          # seed
            ]
            _lib.smg_betweenness.restype = None

            _lib.smg_hits.argtypes = [
                ctypes.c_uint32,                          # n
                ctypes.POINTER(ctypes.c_uint32),          # fwd_offsets
                ctypes.POINTER(ctypes.c_uint32),          # fwd_targets
                ctypes.POINTER(ctypes.c_uint32),          # rev_offsets
                ctypes.POINTER(ctypes.c_uint32),          # rev_targets
                ctypes.POINTER(ctypes.c_double),          # out_hub
                ctypes.POINTER(ctypes.c_double),          # out_auth
                ctypes.c_uint32,                          # iterations
            ]
            _lib.smg_hits.restype = None

            _lib.smg_extract_python.argtypes = [
                ctypes.c_char_p,                          # source
                ctypes.c_uint32,                          # source_len
                ctypes.c_char_p,                          # module_name
                ctypes.c_uint32,                          # module_name_len
                ctypes.c_char_p,                          # file_path
                ctypes.c_uint32,                          # file_path_len
                ctypes.c_char_p,                          # out_buf
                ctypes.c_uint32,                          # out_buf_cap
            ]
            _lib.smg_extract_python.restype = ctypes.c_uint32
    return _lib


def betweenness_centrality_native(
    adj: dict[str, set[str]],
    nodes: set[str],
    sample_threshold: int = 5_000,
    sample_size: int = 500,
) -> dict[str, float] | None:
    """Compute betweenness centrality using the Zig accelerator.

    Returns None if the native library is not available.
    """
    lib = _load()
    if lib is None:
        return None

    node_list = sorted(nodes)
    n = len(node_list)
    if n < 3:
        return {name: 0.0 for name in node_list}

    # Build node -> index mapping
    idx = {name: i for i, name in enumerate(node_list)}

    # Build CSR
    offsets = (ctypes.c_uint32 * (n + 1))()
    edge_list: list[int] = []

    for i, name in enumerate(node_list):
        offsets[i] = len(edge_list)
        for neighbor in sorted(adj.get(name, ())):
            if neighbor in idx:
                edge_list.append(idx[neighbor])
    offsets[n] = len(edge_list)

    m = len(edge_list)
    targets = (ctypes.c_uint32 * m)(*edge_list)
    out_bc = (ctypes.c_double * n)()

    max_sources = 0  # 0 = exact
    if n > sample_threshold:
        max_sources = min(sample_size, n)

    lib.smg_betweenness(n, offsets, targets, out_bc, max_sources, 42)

    return {node_list[i]: out_bc[i] for i in range(n)}


def extract_python_native(
    source: bytes,
    file_path: str,
    module_name: str,
) -> tuple[list, list] | None:
    """Extract Python entities using the Zig accelerator.

    Returns (nodes_data, edges_data) where each is a list of dicts
    parsed from the JSONL output, or None if native lib is unavailable.
    """
    lib = _load()
    if lib is None:
        return None

    raw = _call_extract_python(lib, source, file_path, module_name)
    return _parse_extract_output(raw)


def extract_python_native_batch(
    files: list[tuple[bytes, str, str]],
    max_workers: int = 4,
) -> list[tuple[list, list]] | None:
    """Extract multiple Python files using threaded Zig acceleration.

    files: list of (source_bytes, file_path, module_name) tuples.
    Returns list of (nodes_data, edges_data) per file, or None if unavailable.

    Threading works because the Zig C call releases the Python GIL.
    """
    lib = _load()
    if lib is None:
        return None

    from concurrent.futures import ThreadPoolExecutor

    def _extract_one(args: tuple[bytes, str, str]) -> bytes:
        return _call_extract_python(lib, args[0], args[1], args[2])

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        raw_results = list(pool.map(_extract_one, files))

    return [_parse_extract_output(raw) for raw in raw_results]


def _call_extract_python(lib, source: bytes, file_path: str, module_name: str) -> bytes:
    """Call the Zig extractor and return raw JSONL bytes."""
    buf_cap = 256 * 1024
    buf = ctypes.create_string_buffer(buf_cap)
    mod_bytes = module_name.encode()
    fp_bytes = file_path.encode()

    actual_len = lib.smg_extract_python(
        source, len(source),
        mod_bytes, len(mod_bytes),
        fp_bytes, len(fp_bytes),
        buf, buf_cap,
    )

    return buf.raw[:min(actual_len, buf_cap)]


def _parse_extract_output(raw: bytes) -> tuple[list, list]:
    """Parse JSONL output from native extractor."""
    import json

    if not raw:
        return [], []

    output = raw.decode("utf-8", errors="replace")
    nodes = []
    edges = []
    for line in output.splitlines():
        if not line:
            continue
        record = json.loads(line)
        if record.get("k") == "n":
            nodes.append(record)
        elif record.get("k") == "e":
            edges.append(record)

    return nodes, edges


def _build_directed_csr(
    adj: dict[str, set[str]], node_list: list[str], idx: dict[str, int],
) -> tuple:
    """Build CSR arrays from a directed adjacency dict."""
    n = len(node_list)
    offsets = (ctypes.c_uint32 * (n + 1))()
    edge_list: list[int] = []

    for i, name in enumerate(node_list):
        offsets[i] = len(edge_list)
        for neighbor in sorted(adj.get(name, ())):
            if neighbor in idx:
                edge_list.append(idx[neighbor])
    offsets[n] = len(edge_list)

    m = len(edge_list)
    targets = (ctypes.c_uint32 * m)(*edge_list) if m > 0 else (ctypes.c_uint32 * 1)()
    return offsets, targets


def hits_native(
    fwd: dict[str, set[str]],
    rev: dict[str, set[str]],
    nodes: set[str],
    iterations: int = 50,
) -> dict[str, dict[str, float]] | None:
    """Compute HITS hub/authority scores using the Zig accelerator.

    Returns None if the native library is not available.
    """
    lib = _load()
    if lib is None:
        return None

    node_list = sorted(nodes)
    n = len(node_list)
    if n == 0:
        return {}

    idx = {name: i for i, name in enumerate(node_list)}

    fwd_offsets, fwd_targets = _build_directed_csr(fwd, node_list, idx)
    rev_offsets, rev_targets = _build_directed_csr(rev, node_list, idx)

    out_hub = (ctypes.c_double * n)()
    out_auth = (ctypes.c_double * n)()

    lib.smg_hits(n, fwd_offsets, fwd_targets, rev_offsets, rev_targets,
                 out_hub, out_auth, iterations)

    return {
        node_list[i]: {"hub": round(out_hub[i], 6), "authority": round(out_auth[i], 6)}
        for i in range(n)
    }
