"""
benchmarks/_meta.py

Shared metadata-capture helper for every experiment script under
benchmarks/. Not part of the application — standalone measurement
infrastructure, imported only by scripts in this directory.

Every experiment calls build_run_metadata() once at the start of its run
and embeds the result in its output JSON, so every result file is
self-describing: which commit, which environment, which dependency
versions, without relying on the reader's memory of "which run was this."
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import socket
import subprocess
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def _pip_freeze_subset(names: list[str]) -> dict:
    """Best-effort version lookup for a small set of packages, via
    importlib.metadata — avoids shelling out to pip for every run."""
    versions = {}
    try:
        from importlib import metadata as importlib_metadata
    except ImportError:
        return versions
    for name in names:
        try:
            versions[name] = importlib_metadata.version(name)
        except Exception:
            versions[name] = None
    return versions


def new_run_id(experiment_name: str) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{experiment_name}_{ts}_{short}"


def build_run_metadata(experiment_name: str, extra_packages: list[str] | None = None) -> dict:
    packages = ["redis", "requests", "Flask-SQLAlchemy", "Flask-Mail"]
    if extra_packages:
        packages.extend(extra_packages)

    commit_sha = _git("rev-parse", "HEAD")
    commit_dirty = _git("status", "--porcelain")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")

    return {
        "experiment": experiment_name,
        "run_id": new_run_id(experiment_name),
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "repository": {
            "url": "https://github.com/Devpaul-01/Studyhub",
            "commit_sha": commit_sha,
            "branch": branch,
            "working_tree_clean": (commit_dirty == "") if commit_dirty is not None else None,
        },
        "runtime": {
            "python_version": sys.version,
            "python_implementation": platform.python_implementation(),
        },
        "os": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "hostname_hash": None,  # deliberately not recording the real hostname
        },
        "hardware": {
            "cpu_count_logical": os.cpu_count(),
        },
        "dependency_versions": _pip_freeze_subset(packages),
        "env_notes": {
            "redis_url_var_set": "REDIS_URL" in os.environ,
        },
    }


def redis_server_info(redis_client) -> dict:
    """Capture the real Redis server's own version/mode — distinct from
    the redis-py client library version already in dependency_versions."""
    try:
        info = redis_client.info()
        return {
            "redis_version": info.get("redis_version"),
            "redis_mode": info.get("redis_mode"),
            "os": info.get("os"),
            "connected": True,
        }
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


def write_result(experiment_name: str, run_id: str, payload: dict) -> Path:
    """
    Writes to benchmarks/results/<experiment_name>/<run_id>.json.
    Never overwrites — run_id is unique per call (timestamp + random
    suffix), and this function additionally refuses to clobber an
    existing path as a second safety net.
    """
    out_dir = RESULTS_DIR / experiment_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.json"
    if out_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing result file: {out_path}")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path


def percentile(sorted_values: list[float], pct: float) -> float:
    """Simple nearest-rank percentile over an already-sorted list.
    pct in [0, 100]. Returns None for an empty list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)
