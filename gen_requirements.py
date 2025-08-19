#!/usr/bin/env python3
"""
Generate requirements.txt from imports actually used in the current folder.

- Recursively scans all .py files under the chosen root (default: ".").
- Follows local imports (your own .py files and packages) so they are included in the scan.
- Excludes stdlib and local modules, keeping only third-party packages.
- Resolves distributions and versions from the current Python environment.
- Writes requirements.txt with pinned versions.

Usage:
    python gen_requirements.py --root . --out requirements.txt
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
import sysconfig
import site
from pathlib import Path
from typing import Iterable, Set, Dict, List, Tuple

try:
    # Python 3.8+
    import importlib.metadata as metadata
except Exception:  # pragma: no cover
    import importlib_metadata as metadata  # type: ignore

import importlib.util


# --------------------------- Files & AST scanning --------------------------- #

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".tox", ".venv", "venv", "env", "build", "dist", ".idea", ".vscode"
}
EXCLUDE_FILE_SUFFIXES = {"_pb2.py"}  # add patterns you want ignored


def find_python_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune excluded directories in-place for speed
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".py") and not any(fn.endswith(suf) for suf in EXCLUDE_FILE_SUFFIXES):
                files.append(Path(dirpath) / fn)
    return files


def extract_top_level_imports(py_file: Path) -> Set[str]:
    """
    Return the set of *top-level* module names imported in the given file.
    - 'import a.b.c' -> 'a'
    - 'from x.y import z' -> 'x'
    - relative imports ('from . import x', 'from .pkg import y') are treated as local
    """
    src = py_file.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(src, filename=str(py_file))
    except SyntaxError:
        return set()

    mods: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = (alias.name or "").split(".")[0]
                if name:
                    mods.add(name)
        elif isinstance(node, ast.ImportFrom):
            # Skip relative imports; they refer to local code
            if getattr(node, "level", 0):
                continue
            if node.module:
                name = node.module.split(".")[0]
                if name:
                    mods.add(name)
    return mods


# --------------------------- Classification helpers ------------------------ #

def norm(p: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def get_stdlib_dir() -> str:
    # Works in venvs and system installs
    stdlib = sysconfig.get_paths().get("stdlib")
    return norm(stdlib) if stdlib else ""


def get_site_package_dirs() -> List[str]:
    dirs = set()
    for d in site.getsitepackages() if hasattr(site, "getsitepackages") else []:
        dirs.add(norm(d))
    if hasattr(site, "getusersitepackages"):
        dirs.add(norm(site.getusersitepackages()))
    # Also add purelib/platlib to be safe (venv friendly)
    for key in ("purelib", "platlib"):
        p = sysconfig.get_paths().get(key)
        if p:
            dirs.add(norm(p))
    return sorted(dirs)


def is_local_module(name: str, project_root: Path) -> bool:
    """
    Treat as local if a {name}.py or {name}/__init__.py exists under project root.
    """
    candidates = [
        project_root / f"{name}.py",
        project_root / name / "__init__.py",
    ]
    return any(c.exists() for c in candidates)


def classify_module_origin(name: str, project_root: Path,
                           stdlib_dir: str, site_dirs: List[str]) -> str:
    """
    Return one of: 'stdlib', 'thirdparty', 'local', 'unknown'
    Uses importlib.util.find_spec (no import side-effects).
    """
    # Quick local check by presence of file/package in project
    if is_local_module(name, project_root):
        return "local"

    spec = importlib.util.find_spec(name)
    if spec is None:
        return "unknown"

    # Built-in or frozen → stdlib
    if spec.origin in ("built-in", "frozen"):
        return "stdlib"

    origins: List[str] = []
    if spec.origin:
        origins.append(spec.origin)
    if spec.submodule_search_locations:
        origins.extend(list(spec.submodule_search_locations))

    origins = [norm(p) for p in origins if p]

    # Local project?
    proj = norm(project_root)
    if any(o.startswith(proj + os.sep) or o == proj for o in origins):
        return "local"

    # Stdlib?
    if stdlib_dir and any(o.startswith(stdlib_dir + os.sep) or o == stdlib_dir for o in origins):
        return "stdlib"

    # Site-packages?
    if any(any(o.startswith(sd + os.sep) or o == sd for sd in site_dirs) for o in origins):
        return "thirdparty"

    return "unknown"


# --------------------------- Distribution resolution ----------------------- #

def build_top_to_dists_map() -> Dict[str, List[str]]:
    """
    Map top-level import names -> list of distribution names that provide them.
    Uses packages_distributions() when available; falls back to reading top_level.txt.
    """
    # Preferred (Python 3.10+)
    pkgs_to_dists = {}
    try:
        pkgs_to_dists = metadata.packages_distributions()  # type: ignore[attr-defined]
        if pkgs_to_dists:
            return {k: list(v) for k, v in pkgs_to_dists.items()}
    except Exception:
        pass

    # Fallback: build manually from installed distributions
    mapping: Dict[str, List[str]] = {}
    for dist in metadata.distributions():
        dist_name = dist.metadata.get("Name") or dist.metadata.get("Summary") or ""
        if not dist_name:
            continue
        try:
            top_txt = dist.read_text("top_level.txt")
        except Exception:
            top_txt = None
        if not top_txt:
            # Some packages don't have top_level.txt; try to infer from files
            # (best-effort heuristic)
            files = list(dist.files or [])
            tops = {f.parts[0] for f in files if len(f.parts) >= 1 and f.suffix in {"", ".py"}}
        else:
            tops = {line.strip() for line in top_txt.splitlines() if line.strip()}
        for top in tops:
            mapping.setdefault(top, []).append(dist_name)
    return mapping


def resolve_requirements(modules: Iterable[str]) -> Tuple[Dict[str, str], List[str]]:
    """
    Given top-level module names that are third-party, resolve to {dist: version}.
    Returns: (requirements_dict, unresolved_modules)
    """
    top_to_dists = build_top_to_dists_map()
    reqs: Dict[str, str] = {}
    unresolved: List[str] = []

    for mod in sorted(set(modules)):
        dists = top_to_dists.get(mod)
        if not dists:
            # Not found in metadata map → try a last-resort guess by importing
            # (Still avoid import side effects; we won't import here to be safe.)
            unresolved.append(mod)
            continue

        # Prefer a distribution whose normalized name matches closely.
        # Otherwise take the first.
        chosen = pick_best_dist_for_module(mod, dists)

        try:
            ver = metadata.version(chosen)
        except metadata.PackageNotFoundError:
            unresolved.append(mod)
            continue
        reqs[normalize_dist_name(chosen)] = ver

    return reqs, unresolved


def normalize_dist_name(name: str) -> str:
    # PEP 503 normalization (simplified)
    return name.replace("_", "-")


def pick_best_dist_for_module(mod: str, dists: List[str]) -> str:
    """
    Heuristic to pick a reasonable distribution for a top-level 'mod'.
    """
    mod_l = mod.lower().replace("_", "-")
    scores: List[Tuple[int, str]] = []
    for d in dists:
        d_l = d.lower().replace("_", "-")
        score = 0
        if d_l == mod_l:
            score += 100
        if mod_l in d_l:
            score += 10
        # Shorter names slightly preferred
        score -= len(d_l)
        scores.append((score, d))
    scores.sort(reverse=True)
    return scores[0][1]


# --------------------------- Main pipeline --------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate requirements.txt from imports.")
    parser.add_argument("--root", default=".", help="Project root to scan (default: current folder).")
    parser.add_argument("--out", default="requirements.txt", help="Output file path.")
    args = parser.parse_args()

    project_root = Path(args.root).resolve()
    stdlib_dir = get_stdlib_dir()
    site_dirs = get_site_package_dirs()

    py_files = find_python_files(project_root)
    if not py_files:
        print(f"[INFO] No Python files found under: {project_root}")
        sys.exit(0)

    all_imports: Set[str] = set()
    for f in py_files:
        all_imports |= extract_top_level_imports(f)

    third_party_mods: Set[str] = set()
    skipped_local: Set[str] = set()
    skipped_stdlib: Set[str] = set()
    unknown_kind: Set[str] = set()

    for name in sorted(all_imports):
        kind = classify_module_origin(name, project_root, stdlib_dir, site_dirs)
        if kind == "thirdparty":
            third_party_mods.add(name)
        elif kind == "local":
            skipped_local.add(name)
        elif kind == "stdlib":
            skipped_stdlib.add(name)
        else:
            # Unknown: try to resolve via metadata; if it resolves, we’ll include it later
            unknown_kind.add(name)

    # Try to resolve unknowns via metadata map (some may be third-party without spec.origin)
    reqs_map, unresolved = resolve_requirements(third_party_mods | unknown_kind)

    # Write requirements.txt
    out_path = Path(args.out)
    lines = [f"{dist}=={ver}" for dist, ver in sorted(reqs_map.items(), key=lambda x: x[0].lower())]
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    print(f"[OK] Wrote {out_path} with {len(lines)} package(s).")

    # Helpful diagnostics
    if skipped_local:
        print(f"[INFO] Local modules (excluded): {', '.join(sorted(skipped_local))}")
    if skipped_stdlib:
        print(f"[INFO] Stdlib modules (excluded): {', '.join(sorted(skipped_stdlib))}")
    unresolved_set = set(unresolved) - set(reqs_map.keys())
    if unresolved_set:
        print("[WARN] Some imports couldn't be mapped to installed distributions:")
        print("       " + ", ".join(sorted(unresolved_set)))
        print("       If these are local modules, ignore. If third-party, make sure they’re installed.")


if __name__ == "__main__":
    main()
