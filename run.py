#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parent
DEFAULT_VERSIONS_FILE = ROOT / "versions.txt"
PATCHES_ROOT = ROOT / "patches"
TMP_ROOT = Path(os.environ.get("MYLIBBUILDER_ROOT", ROOT / "build"))
TARGETS = ["esp32", "esp32s2", "esp32s3", "esp32c2", "esp32c3", "esp32c6", "esp32h2", "esp32p4", "esp32p4_es", "esp32c5", "esp32c61"]
REPOSITORY_URLS = {
    "esp-idf": "https://github.com/espressif/esp-idf.git",
    "esp32-arduino-lib-builder": "https://github.com/espressif/esp32-arduino-lib-builder.git",
    "arduino-esp32": "https://github.com/espressif/arduino-esp32.git",
    "tinyusb": "https://github.com/hathach/tinyusb.git",
}
BUILDER_DIR = TMP_ROOT / "esp32-arduino-lib-builder"
SUBMODULES = {
    "esp-idf": TMP_ROOT / "esp-idf",
    "esp32-arduino-lib-builder": BUILDER_DIR,
    # build.sh always looks for the arduino-esp32 sources at components/arduino
    # (relative to its own cwd), so clone straight there instead of a separate
    # temp dir: that way the checkout/patches applied by run.py are what
    # build.sh actually compiles against.
    "arduino-esp32": BUILDER_DIR / "components" / "arduino",
    # Same idea for TinyUSB: build.sh's tools/update-components.sh clones it to
    # components/arduino_tinyusb/tinyusb and pulls master (unpinned). Clone it
    # here pinned to versions.txt; the update-components.sh patch then skips the
    # upstream pull so the pinned commit (matching the arduino-esp32 sources) is
    # what actually compiles.
    "tinyusb": BUILDER_DIR / "components" / "arduino_tinyusb" / "tinyusb",
}


def parse_versions(text: str) -> Dict[str, Dict[str, object]]:
    repos: Dict[str, Tuple[str, str]] = {}
    components: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"lib-builder", "esp-idf", "arduino", "tinyusb"}:
            parts = value.split()
            if len(parts) >= 2:
                repos[key] = (parts[0], parts[1])
        elif key.startswith("espressif__") or key.startswith("chmorgan__") or key.startswith("joltwallet__"):
            components[key] = value
    return {"repos": repos, "components": components}


def run_git(repo_path: Path, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo_path), *args]
    print(f"[git] {' '.join(cmd)}", flush=True)
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
    else:
        result = subprocess.run(cmd)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd, output=getattr(result, "stdout", None), stderr=getattr(result, "stderr", None))
    return result


def checkout_submodule_version(repo_path: Path, version: Tuple[str, str], repo_name: str | None = None) -> None:
    if not repo_path.exists():
        repo_name = repo_name or repo_path.name
        repo_url = REPOSITORY_URLS.get(repo_name)
        if not repo_url:
            raise FileNotFoundError(f"Repository checkout target not found: {repo_path}")
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[clone] {repo_url} -> {repo_path}", flush=True)
        # Blobless partial clone: fetch full commit/tree history (needed to resolve
        # the pinned branch/tag/commit) but skip historical file blobs, which are
        # what make esp-idf/.git (~3.3GB) and arduino/.git (~2.2GB) huge. Blobs for
        # the checked-out commit are fetched lazily on demand.
        subprocess.run(["git", "clone", "--filter=blob:none", repo_url, str(repo_path)], check=True)

    remotes = run_git(repo_path, "remote", capture=True).stdout.splitlines()
    remote_name = None
    if "upstream" in remotes:
        remote_name = "upstream"
    elif "origin" in remotes:
        remote_name = "origin"

    if remote_name is None:
        raise RuntimeError(f"Submodule {repo_path} has no configured remotes")

    remote_url = run_git(repo_path, "remote", "get-url", remote_name, capture=True).stdout.strip()

    if remote_name == "origin" and "upstream" not in remotes:
        run_git(repo_path, "remote", "add", "upstream", remote_url)
        remote_name = "upstream"

    run_git(repo_path, "fetch", remote_name, "--tags", "--prune", "--force", "--recurse-submodules=no", check=False)

    if run_git(repo_path, "rev-parse", "--is-shallow-repository", capture=True).stdout.strip() == "true":
        run_git(repo_path, "fetch", "--unshallow", remote_name, "--tags", "--prune", "--force", "--recurse-submodules=no", check=False)

    branch, commit = version
    if branch and branch != "master":
        run_git(repo_path, "fetch", remote_name, branch, "--force")
        run_git(repo_path, "checkout", branch)

    if commit:
        commit_check = subprocess.run(["git", "-C", str(repo_path), "rev-parse", "--verify", commit], check=False, capture_output=True, text=True)
        if commit_check.returncode != 0:
            raise RuntimeError(f"Commit '{commit}' was not found in {repo_path} after fetching from {remote_name}")
        run_git(repo_path, "checkout", commit)


def materialize_git_submodules(repo_path: Path) -> None:
    """Download git submodules for a prepared checkout before it is archived for CI jobs."""
    if not (repo_path / ".git").exists():
        return
    gitmodules = repo_path / ".gitmodules"
    if not gitmodules.exists():
        return
    run_git(repo_path, "submodule", "sync", "--recursive")
    run_git(repo_path, "submodule", "update", "--init", "--recursive")


_DEPENDENCY_KEY_RE_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _dependency_key_pattern(dep_key: str) -> "re.Pattern[str]":
    pattern = _DEPENDENCY_KEY_RE_CACHE.get(dep_key)
    if pattern is None:
        pattern = re.compile(r"^(?P<indent>[ \t]*)" + re.escape(dep_key) + r":[ \t]*(?P<value>.*)$")
        _DEPENDENCY_KEY_RE_CACHE[dep_key] = pattern
    return pattern


_VERSION_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)version:[ \t]*(?P<value>[^#]*?)[ \t]*(?P<comment>#.*)?$")


def _set_dependency_version(lines: list, dep_key: str, version: str) -> bool:
    """Set the pinned version for an `owner/name:` dependency in an idf_component.yml
    style manifest, touching only that dependency's own `version:` field so unrelated
    text elsewhere in the file (including other dependencies whose name happens to be
    a substring match) is left untouched."""
    key_pattern = _dependency_key_pattern(dep_key)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        match = key_pattern.match(stripped)
        if not match:
            continue
        indent = match.group("indent")
        inline_value = match.group("value").strip()
        if inline_value:
            # shorthand form: "owner/name: <constraint>"
            new_line = f'{indent}{dep_key}: "{version}"\n'
            if line == new_line:
                return False
            lines[i] = new_line
            return True
        # block form: the version lives on a direct child line, e.g.
        #   owner/name:
        #     version: "<constraint>"
        #     rules: ...
        # Dependencies with multiple conditional versions (a "matches:" list
        # instead of a direct "version:" child) are intentionally left alone,
        # since there is no single value to pin.
        child_indent = None
        for j in range(i + 1, len(lines)):
            sub = lines[j].rstrip("\n")
            if not sub.strip():
                continue
            sub_indent = len(sub) - len(sub.lstrip(" \t"))
            if sub_indent <= len(indent):
                break
            if child_indent is None:
                child_indent = sub_indent
            if sub_indent != child_indent:
                continue
            version_match = _VERSION_LINE_RE.match(sub)
            if version_match:
                comment = version_match.group("comment")
                suffix = f" {comment}" if comment else ""
                new_sub = f'{" " * child_indent}version: "{version}"{suffix}\n'
                if lines[j] == new_sub:
                    return False
                lines[j] = new_sub
                return True
        return False
    return False


def update_component_versions(repo_path: Path, versions: Dict[str, str]) -> None:
    if not repo_path.exists():
        return
    component_files = [
        repo_path / "idf_component.yml",
        repo_path / "components" / "idf_component.yml",
        repo_path / "dependencies.lock",
    ]
    for component_file in component_files:
        if not component_file.exists():
            continue
        lines = component_file.read_text(encoding="utf-8").splitlines(keepends=True)
        changed = False
        for name, version in versions.items():
            if not version:
                continue
            if not (name.startswith("espressif__") or name.startswith("chmorgan__") or name.startswith("joltwallet__")):
                continue
            dep_key = name.replace("__", "/", 1)
            if _set_dependency_version(lines, dep_key, version):
                changed = True
        if changed:
            component_file.write_text("".join(lines), encoding="utf-8")
            print(f"Updated component versions in {component_file}")


def _normalize_newlines(data: bytes) -> bytes:
    """Convert CRLF/CR line endings to LF for text content. Binary files
    (detected by a NUL byte) are returned untouched. Patch files are edited on
    Windows but consumed by a Linux build (bash scripts, sdkconfig appends), so
    stray '\\r' bytes must never reach the build or bash breaks with
    "$'\\r': command not found"."""
    if b"\x00" in data:
        return data
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def apply_repo_patches(repo_dir: Path, patch_dir: Path) -> None:
    if not patch_dir.exists():
        return
    for patch_path in sorted(patch_dir.rglob("*")):
        if patch_path.name.startswith(".") or not patch_path.is_file():
            continue
        rel_path = patch_path.relative_to(patch_dir)
        if patch_path.suffix == ".append":
            target_path = repo_dir / rel_path.with_suffix("")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            patch_content = _normalize_newlines(patch_path.read_bytes())
            if target_path.exists():
                target_path.write_bytes(_normalize_newlines(target_path.read_bytes()) + patch_content)
            else:
                target_path.write_bytes(patch_content)
        elif patch_path.suffix in {".diff", ".patch"}:
            subprocess.run(["git", "-C", str(repo_dir), "apply", str(patch_path)], check=True, stdout=subprocess.DEVNULL)
        else:
            target_path = repo_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(_normalize_newlines(patch_path.read_bytes()))


def ensure_system_dependencies() -> None:
    if shutil.which("apt-get") is None:
        return
    sudo_cmd = ["sudo"] if shutil.which("sudo") else []
    try:
        subprocess.run(sudo_cmd + ["apt-get", "update"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        pass
    subprocess.run(
        sudo_cmd + ["apt-get", "install", "-y", "libusb-1.0-0", "libusb-1.0-0-dev", "libudev1", "libftdi1-2"],
        check=False,
    )


def ensure_idf_environment() -> None:
    idf_dir = SUBMODULES["esp-idf"]
    if not idf_dir.exists():
        raise FileNotFoundError(f"ESP-IDF checkout not found: {idf_dir}")
    install_script = idf_dir / "install.sh"
    if not install_script.exists():
        raise FileNotFoundError(f"ESP-IDF install script not found: {install_script}")
    subprocess.run(["bash", str(install_script)], cwd=idf_dir, check=True)


def enable_local_ccache(env: Dict[str, str]) -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        return
    if shutil.which("ccache") is None:
        return
    env.setdefault("IDF_CCACHE_ENABLE", "1")


def build_target(target: str | None = None) -> None:
    builder_dir = SUBMODULES["esp32-arduino-lib-builder"]
    if not builder_dir.exists():
        raise FileNotFoundError(f"Builder checkout not found: {builder_dir}")
    ensure_system_dependencies()
    ensure_idf_environment()
    env = os.environ.copy()
    env["IDF_PATH"] = str(SUBMODULES["esp-idf"])
    env["AR_SOURCE_BRANCH"] = "master"
    env["IDF_BRANCH"] = "master"
    enable_local_ccache(env)
    cmd = ["bash", "build.sh", "-s", "-e"]
    if target:
        cmd[2:2] = ["-t", target]
    subprocess.run(cmd, cwd=builder_dir, check=True, env=env)


def versions_file_for(target: str) -> Path:
    """Return the per-target versions file (versions.<target>) when it exists,
    otherwise fall back to the shared versions.txt."""
    target_file = ROOT / f"versions.{target}"
    if target_file.exists():
        return target_file
    return DEFAULT_VERSIONS_FILE


def confirm_build_all_targets() -> None:
    targets = ", ".join(TARGETS)
    print("[confirm] nenhum target foi informado.", flush=True)
    print(f"[confirm] O build.sh sera executado sem -t e fara o build sequencial de todos os targets disponiveis: {targets}.", flush=True)
    print("[confirm] Isso pode demorar muito.", flush=True)
    answer = input("Digite 'sim' para confirmar e continuar: ").strip().lower()
    if answer not in {"sim", "s"}:
        print("[confirm] build cancelado.", flush=True)
        sys.exit(1)


def prepare_sources(versions: Dict[str, Dict[str, object]]) -> None:
    repo_versions = {
        "lib-builder": versions["repos"].get("lib-builder", ("master", "")),
        "esp-idf": versions["repos"].get("esp-idf", ("master", "")),
        "arduino": versions["repos"].get("arduino", ("master", "")),
        "tinyusb": versions["repos"].get("tinyusb", ("master", "")),
    }

    checkout_submodule_version(SUBMODULES["esp-idf"], repo_versions["esp-idf"], repo_name="esp-idf")
    checkout_submodule_version(SUBMODULES["esp32-arduino-lib-builder"], repo_versions["lib-builder"], repo_name="esp32-arduino-lib-builder")
    checkout_submodule_version(SUBMODULES["arduino-esp32"], repo_versions["arduino"], repo_name="arduino-esp32")
    checkout_submodule_version(SUBMODULES["tinyusb"], repo_versions["tinyusb"], repo_name="tinyusb")

    update_component_versions(SUBMODULES["esp-idf"], versions["components"])
    update_component_versions(SUBMODULES["arduino-esp32"], versions["components"])

    for repo_name, patch_dir_name in [("esp-idf", "esp-idf"), ("arduino-esp32", "arduino-esp32"), ("esp32-arduino-lib-builder", "esp32-arduino-lib-builder")]:
        patch_dir = PATCHES_ROOT / patch_dir_name
        apply_repo_patches(SUBMODULES[repo_name], patch_dir)

    for repo_path in SUBMODULES.values():
        materialize_git_submodules(repo_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESP32 Arduino libraries from pinned versions")
    parser.add_argument("-t", "--target", choices=TARGETS, help="Target to build")
    parser.add_argument(
        "--install-only",
        action="store_true",
        help="Only checkout esp-idf and install its toolchain (~/.espressif), then exit. "
        "Used to warm the CI cache without running a build.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only checkout pinned repositories, apply component versions/patches, download git submodules, then exit.",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Skip repository checkout/patch preparation and build from an already prepared source tree.",
    )
    args = parser.parse_args()

    selected_modes = sum(1 for mode in [args.install_only, args.prepare_only, args.build_only] if mode)
    if selected_modes > 1:
        parser.error("--install-only, --prepare-only and --build-only cannot be combined")

    if not args.install_only and not args.prepare_only and not args.target:
        confirm_build_all_targets()

    versions_file = versions_file_for(args.target) if args.target else DEFAULT_VERSIONS_FILE
    print(f"[versions] using {versions_file.name}", flush=True)
    versions = parse_versions(versions_file.read_text(encoding="utf-8"))

    if args.install_only:
        repo_versions = {
            "esp-idf": versions["repos"].get("esp-idf", ("master", "")),
        }
        checkout_submodule_version(SUBMODULES["esp-idf"], repo_versions["esp-idf"], repo_name="esp-idf")
        # Toolchain (~/.espressif) is target-independent: install.sh installs the
        # tools required by this esp-idf checkout for every target at once.
        ensure_idf_environment()
        print("[install-only] toolchain ready, skipping build", flush=True)
        return

    if not args.build_only:
        prepare_sources(versions)

    if args.prepare_only:
        print("[prepare-only] sources ready, skipping build", flush=True)
        return

    build_target(args.target)


if __name__ == "__main__":
    main()
