"""Access bundled MicroVM examples and documentation."""

from __future__ import annotations

import shutil
import sysconfig
from pathlib import Path, PurePosixPath


BUNDLE_ITEMS = (
    "README.md",
    "ARCHITECTURE.md",
    "Dockerfile",
    "docker-compose.yml",
    "LICENSE.md",
    "docs",
    "examples",
)
EXCLUDED_DIRS = {".git", ".runtime", "build", "dist", "__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


class BundleError(Exception):
    """Expected bundled resource failure."""


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _installed_data_roots() -> tuple[Path, ...]:
    data_path = sysconfig.get_path("data")
    if not data_path:
        raise BundleError("Python installation does not expose a data directory")
    root = Path(data_path)
    return (
        root / "m",
        root / "microvm",
        root / "share" / "microvm",
    )


def bundle_root() -> Path:
    candidates = (_source_root(), *_installed_data_roots())
    for candidate in candidates:
        if (candidate / "README.md").is_file() and (candidate / "examples").is_dir():
            return candidate
    checked = ", ".join(str(candidate) for candidate in candidates)
    raise BundleError(f"MicroVM bundled resources were not found. Checked: {checked}")


def is_excluded(relative_path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in relative_path.parts):
        return True
    if relative_path.suffix in EXCLUDED_SUFFIXES:
        return True
    return relative_path.name.endswith(".microvm.zip")


def ensure_safe_relative_path(relative_path: Path) -> PurePosixPath:
    posix_path = PurePosixPath(relative_path.as_posix())
    if posix_path.is_absolute() or any(part in ("", ".", "..") for part in posix_path.parts):
        raise BundleError(f"unsafe bundled resource path: {relative_path}")
    return posix_path


def iter_bundle_files(root: Path | None = None) -> list[Path]:
    resolved_root = root or bundle_root()
    files: list[Path] = []

    for item in BUNDLE_ITEMS:
        source = resolved_root / item
        if not source.exists():
            continue
        candidates = [source] if source.is_file() else sorted(path for path in source.rglob("*") if path.is_file())
        for path in candidates:
            relative = path.relative_to(resolved_root)
            if is_excluded(relative):
                continue
            ensure_safe_relative_path(relative)
            files.append(relative)

    return sorted(files, key=lambda path: path.as_posix())


def list_bundle_resources() -> list[str]:
    return [path.as_posix() for path in iter_bundle_files()]


def extract_bundle(destination: Path, overwrite: bool = False) -> list[str]:
    root = bundle_root()
    destination = destination.resolve()
    files = iter_bundle_files(root)

    conflicts = [relative.as_posix() for relative in files if (destination / relative).exists()]
    if conflicts and not overwrite:
        preview = "\n- ".join(conflicts[:10])
        if len(conflicts) > 10:
            preview += f"\n- ... and {len(conflicts) - 10} more"
        raise BundleError(f"refusing to overwrite existing bundled files:\n- {preview}")

    for relative in files:
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    return [path.as_posix() for path in files]
