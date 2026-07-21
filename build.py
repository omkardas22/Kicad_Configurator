"""
build.py — One-Click Build Orchestrator for KiCad Constraint Configurator
==========================================================================
Run this script from the repository root on a Windows machine to:
  1. Verify / install required pip packages.
  2. Compile the application with PyInstaller (--onedir, --noconsole).
  3. Zip the dist folder into releases/v1.0.0/app_payload.zip.
  4. Auto-detect Inno Setup Compiler (ISCC.exe).
  5. Compile setup_offline.iss  → releases/v1.0.0/standalone_installer/
  6. Compile setup_web.iss      → releases/v1.0.0/web_installer/

Usage:
  python build.py
  python build.py --dry-run      # Validate paths without compiling
  python build.py --skip-inno    # Skip Inno Setup compilation
  python build.py --version 1.2.0
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional

# Force UTF-8 output on Windows consoles (avoids cp1252 UnicodeEncodeError)
os.environ["PYTHONUTF8"] = "1"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
APP_VERSION = "1.0.0"
APP_NAME = "KiCadConstraintConfigurator"
ENTRY_SCRIPT = "src/main.py"
TEMPLATE_DIR = "kicad_template"
BUILD_SCRIPTS_DIR = "build_scripts"

REQUIRED_PACKAGES = [
    "customtkinter>=5.2.0",
    "google-genai>=1.0.0",
    "requests>=2.31.0",
    "beautifulsoup4>=4.12.0",
    "pydantic>=2.0.0",
    "pyinstaller>=6.0.0",
]

ISCC_SEARCH_PATHS = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    r"C:\Program Files\Inno Setup 5\ISCC.exe",
]

# Colour helpers for terminal output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log(msg: str, colour: str = "") -> None:
    try:
        print(f"{colour}{msg}{RESET}" if colour else msg, flush=True)
    except UnicodeEncodeError:
        # Fallback: strip non-ASCII for terminals that can't handle Unicode
        safe = msg.encode("ascii", errors="replace").decode("ascii")
        print(f"{colour}{safe}{RESET}" if colour else safe, flush=True)


def log_step(step: str, total: int, current: int, msg: str) -> None:
    log(f"\n{BOLD}{CYAN}[{current}/{total}] {step}{RESET}")
    log(f"    {msg}")


def log_ok(msg: str) -> None:
    log(f"  [OK]  {msg}", GREEN)


def log_warn(msg: str) -> None:
    log(f"  [WARN] {msg}", YELLOW)


def log_err(msg: str) -> None:
    log(f"  [FAIL] {msg}", RED)


def run(cmd: list[str], cwd: Optional[Path] = None, check: bool = True) -> int:
    """Run a subprocess command, streaming output."""
    log(f"  $ {' '.join(str(c) for c in cmd)}", CYAN)
    result = subprocess.run(cmd, cwd=cwd, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed with code {result.returncode}: {' '.join(str(c) for c in cmd)}")
    return result.returncode


# ---------------------------------------------------------------------------
# Step 1 — Verify / install packages
# ---------------------------------------------------------------------------
def step_install_packages(dry_run: bool) -> None:
    log_step("Install Packages", 6, 1, "Checking & installing required pip packages")
    if dry_run:
        log_warn("DRY RUN — skipping pip install")
        return
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + REQUIRED_PACKAGES
    run(cmd)
    log_ok("All packages installed / up to date")


# ---------------------------------------------------------------------------
# Step 2 — PyInstaller build
# ---------------------------------------------------------------------------
def step_pyinstaller(root: Path, version: str, dry_run: bool) -> Path:
    log_step("PyInstaller Build", 6, 2, "Compiling application with PyInstaller")

    dist_path = root / "dist"
    build_path = root / "build_pyinstaller"
    app_dir = dist_path / APP_NAME

    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        "--noconsole",
        "--onedir",
        f"--name={APP_NAME}",
        f"--distpath={dist_path}",
        f"--workpath={build_path}",
        f"--specpath={build_path}",
        f"--add-data={root / TEMPLATE_DIR}{os.pathsep}{TEMPLATE_DIR}",
        "--noconfirm",
        "--clean",
        str(root / ENTRY_SCRIPT),
    ]

    if dry_run:
        log_warn("DRY RUN — skipping PyInstaller build")
        return app_dir

    if platform.system() != "Windows":
        log_warn("PyInstaller builds are Windows-only. Skipping on this OS.")
        return app_dir

    run(pyinstaller_args, cwd=root)
    if not app_dir.exists():
        raise RuntimeError(f"PyInstaller output not found: {app_dir}")
    log_ok(f"PyInstaller output: {app_dir}")
    return app_dir


# ---------------------------------------------------------------------------
# Step 3 — Zip payload
# ---------------------------------------------------------------------------
def step_zip_payload(app_dir: Path, root: Path, version: str, dry_run: bool) -> Path:
    log_step("Create Payload Zip", 6, 3, "Zipping PyInstaller output → app_payload.zip")

    release_dir = root / "releases" / f"v{version}"
    release_dir.mkdir(parents=True, exist_ok=True)
    zip_path = release_dir / "app_payload.zip"

    if dry_run:
        log_warn(f"DRY RUN — would create {zip_path}")
        return zip_path

    if not app_dir.exists():
        log_warn(f"App dir {app_dir} not found — skipping zip (run without --dry-run first)")
        return zip_path

    log(f"    Zipping {app_dir} → {zip_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in app_dir.rglob("*"):
            if file_path.is_file():
                arcname = Path(APP_NAME) / file_path.relative_to(app_dir)
                zf.write(file_path, arcname)

    size_mb = zip_path.stat().st_size / 1024 / 1024
    log_ok(f"Created {zip_path.name} ({size_mb:.1f} MB)")
    return zip_path


# ---------------------------------------------------------------------------
# Step 4 — Find ISCC
# ---------------------------------------------------------------------------
def step_find_iscc() -> Optional[Path]:
    log_step("Locate Inno Setup Compiler", 6, 4, "Searching for ISCC.exe")

    # Try PATH first
    iscc_in_path = shutil.which("ISCC")
    if iscc_in_path:
        log_ok(f"Found in PATH: {iscc_in_path}")
        return Path(iscc_in_path)

    # Try common Windows install paths
    for p in ISCC_SEARCH_PATHS:
        if Path(p).exists():
            log_ok(f"Found: {p}")
            return Path(p)

    log_warn("ISCC.exe not found. Install Inno Setup 6 from https://jrsoftware.org/isdl.php")
    return None


# ---------------------------------------------------------------------------
# Step 5 — Compile Inno Setup scripts
# ---------------------------------------------------------------------------
def step_compile_inno(
    iscc: Optional[Path],
    root: Path,
    version: str,
    dry_run: bool,
    skip_inno: bool,
) -> None:
    log_step("Compile Inno Setup Installers", 6, 5, "Building offline + web setup executables")

    if skip_inno:
        log_warn("--skip-inno flag set. Skipping Inno Setup compilation.")
        return

    if not iscc:
        log_warn("No ISCC found — skipping installer compilation.")
        return

    release_dir = root / "releases" / f"v{version}"
    offline_out = release_dir / "standalone_installer"
    web_out = release_dir / "web_installer"
    offline_out.mkdir(parents=True, exist_ok=True)
    web_out.mkdir(parents=True, exist_ok=True)

    scripts = [
        (root / BUILD_SCRIPTS_DIR / "setup_offline.iss", offline_out),
        (root / BUILD_SCRIPTS_DIR / "setup_web.iss",     web_out),
    ]

    for iss_path, out_dir in scripts:
        if not iss_path.exists():
            log_warn(f"Script not found: {iss_path}")
            continue

        if dry_run:
            log_warn(f"DRY RUN — would compile {iss_path.name} → {out_dir}")
            continue

        cmd = [
            str(iscc),
            f"/DAppVersion={version}",
            f"/DAppName={APP_NAME}",
            f"/DRootDir={root}",
            f"/DOutputDir={out_dir}",
            str(iss_path),
        ]
        try:
            run(cmd, cwd=root)
            log_ok(f"Compiled {iss_path.name} → {out_dir}")
        except RuntimeError as exc:
            log_err(f"Failed to compile {iss_path.name}: {exc}")


# ---------------------------------------------------------------------------
# Step 6 — Summary
# ---------------------------------------------------------------------------
def step_summary(root: Path, version: str) -> None:
    log_step("Build Summary", 6, 6, "Checking release output")
    release_dir = root / "releases" / f"v{version}"

    log(f"\n  📦 Release directory: {release_dir}")
    if release_dir.exists():
        for item in sorted(release_dir.rglob("*")):
            if item.is_file():
                size_kb = item.stat().st_size / 1024
                rel = item.relative_to(release_dir)
                log(f"      {rel}  ({size_kb:.1f} KB)")
    else:
        log_warn("Release directory does not exist yet (run without --dry-run).")

    log(f"\n{BOLD}{GREEN}{'='*60}{RESET}")
    log(f"{BOLD}{GREEN}  Build complete! v{version}{RESET}")
    log(f"{BOLD}{GREEN}{'='*60}{RESET}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-click build orchestrator for KiCad Constraint Configurator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate without compiling")
    parser.add_argument("--skip-inno", action="store_true", help="Skip Inno Setup compilation")
    parser.add_argument("--version", default=APP_VERSION, help=f"Version string (default: {APP_VERSION})")
    args = parser.parse_args()

    version: str = args.version
    dry_run: bool = args.dry_run
    skip_inno: bool = args.skip_inno

    root = Path(__file__).parent.resolve()

    log(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    log(f"{BOLD}{CYAN}  KiCad Constraint Configurator — Build System v{version}{RESET}")
    log(f"{BOLD}{CYAN}  Root: {root}{RESET}")
    if dry_run:
        log(f"{BOLD}{YELLOW}  ⚠  DRY RUN MODE — no files will be modified{RESET}")
    log(f"{BOLD}{CYAN}{'='*60}{RESET}\n")

    try:
        step_install_packages(dry_run)
        app_dir = step_pyinstaller(root, version, dry_run)
        zip_path = step_zip_payload(app_dir, root, version, dry_run)
        iscc = step_find_iscc()
        step_compile_inno(iscc, root, version, dry_run, skip_inno)
        step_summary(root, version)
    except Exception as exc:
        log_err(f"Build FAILED: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
