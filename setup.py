import os
from pathlib import Path

from setuptools import setup

try:
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # pragma: no cover - fallback for older build environments.
    try:
        from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
    except ImportError:
        _bdist_wheel = None


BUNDLE_ITEMS = (
    "README.md",
    "ARCHITECTURE.md",
    "Dockerfile",
    "docker-compose.yml",
    "LICENSE.md",
    "docs",
    "examples",
)
BUNDLE_DATA_DIR = Path("m")
EXCLUDED_DIRS = {".git", ".runtime", "build", "dist", "__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


if _bdist_wheel is not None:

    class MicroVMBdistWheel(_bdist_wheel):
        def finalize_options(self) -> None:
            super().finalize_options()
            if os.name == "nt":
                self.bdist_dir = os.environ.get("MICROVM_WHEEL_BUILD_DIR", "w")


CMDCLASS = {"bdist_wheel": MicroVMBdistWheel} if _bdist_wheel is not None else {}


def is_excluded(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    return path.name.endswith(".microvm.zip")


def collect_bundle_data_files() -> list[tuple[str, list[str]]]:
    root = Path(__file__).parent
    grouped: dict[str, list[str]] = {}

    for item in BUNDLE_ITEMS:
        source = root / item
        if not source.exists():
            continue

        files = [source] if source.is_file() else sorted(path for path in source.rglob("*") if path.is_file())
        for path in files:
            relative = path.relative_to(root)
            if is_excluded(relative):
                continue
            destination = BUNDLE_DATA_DIR / relative.parent
            grouped.setdefault(destination.as_posix(), []).append(relative.as_posix())

    return [(destination, sorted(paths)) for destination, paths in sorted(grouped.items())]


setup(data_files=collect_bundle_data_files(), cmdclass=CMDCLASS)
