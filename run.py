#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parent
VERSIONS_FILE = ROOT / "versions.txt"
PATCHES_ROOT = ROOT / "patches"
TMP_ROOT = Path("/tmp/myLibBuilder-repos")
REPOSITORY_URLS = {
    "esp-idf": "https://github.com/espressif/esp-idf.git",
    "esp32-arduino-lib-builder": "https://github.com/espressif/esp32-arduino-lib-builder.git",
    "arduino-esp32": "https://github.com/espressif/arduino-esp32.git",
}
SUBMODULES = {
    name: TMP_ROOT / name for name in REPOSITORY_URLS
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
        if key in {"lib-builder", "esp-idf", "arduino"}:
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
        subprocess.run(["git", "clone", repo_url, str(repo_path)], check=True)

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
        text = component_file.read_text(encoding="utf-8")
        updated = text
        for name, version in versions.items():
            if name in {"espressif__cbor", "espressif__cjson", "espressif__dl_fft", "espressif__esp-dsp", "espressif__esp-modbus", "espressif__esp-nn", "espressif__esp-serial-flasher", "espressif__esp-sr", "espressif__esp-tflite-micro", "espressif__esp-zboss-lib", "espressif__esp-zigbee-lib", "espressif__esp_delta_ota", "espressif__esp_diag_data_store", "espressif__esp_diagnostics", "espressif__esp_encrypted_img", "espressif__esp_insights", "espressif__esp_jpeg", "espressif__esp_matter", "espressif__esp_modem", "espressif__esp_rainmaker", "espressif__esp_rcp_update", "espressif__esp_schedule", "espressif__esp_secure_cert_mgr", "espressif__jsmn", "espressif__json_generator", "espressif__json_parser", "espressif__libsodium", "espressif__mdns", "espressif__network_provisioning", "espressif__qrcode", "espressif__rmaker_common"}:
                if name.replace("espressif__", "") in updated:
                    updated = updated.replace(name.replace("espressif__", ""), version)
        if updated != text:
            component_file.write_text(updated, encoding="utf-8")
            print(f"Updated component versions in {component_file}")


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
            patch_content = patch_path.read_text(encoding="utf-8")
            if target_path.exists():
                target_path.write_text(target_path.read_text(encoding="utf-8") + patch_content, encoding="utf-8")
            else:
                target_path.write_text(patch_content, encoding="utf-8")
        elif patch_path.suffix in {".diff", ".patch"}:
            subprocess.run(["git", "-C", str(repo_dir), "apply", str(patch_path)], check=True, stdout=subprocess.DEVNULL)
        else:
            target_path = repo_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(patch_path, target_path)


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


def build_target(target: str) -> None:
    builder_dir = SUBMODULES["esp32-arduino-lib-builder"]
    if not builder_dir.exists():
        raise FileNotFoundError(f"Builder checkout not found: {builder_dir}")
    ensure_system_dependencies()
    ensure_idf_environment()
    env = os.environ.copy()
    env["IDF_PATH"] = str(SUBMODULES["esp-idf"])
    env["AR_SOURCE_BRANCH"] = "master"
    env["IDF_BRANCH"] = "master"
    subprocess.run(["bash", "build.sh", "-t", target, "-b", "build"], cwd=builder_dir, check=True, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ESP32 Arduino libraries from pinned versions")
    parser.add_argument("-t", "--target", required=True, choices=["esp32", "esp32s2", "esp32s3", "esp32c2", "esp32c3", "esp32c6", "esp32h2", "esp32p4", "esp32p4_es", "esp32c5", "esp32c61"], help="Target to build")
    args = parser.parse_args()

    versions = parse_versions(VERSIONS_FILE.read_text(encoding="utf-8"))
    for name, repo_path in SUBMODULES.items():
        if not repo_path.exists():
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[prepare] repository checkout will be created at {repo_path}", flush=True)

    repo_versions = {
        "lib-builder": versions["repos"].get("lib-builder", ("master", "")),
        "esp-idf": versions["repos"].get("esp-idf", ("master", "")),
        "arduino": versions["repos"].get("arduino", ("master", "")),
    }

    checkout_submodule_version(SUBMODULES["esp-idf"], repo_versions["esp-idf"], repo_name="esp-idf")
    checkout_submodule_version(SUBMODULES["esp32-arduino-lib-builder"], repo_versions["lib-builder"], repo_name="esp32-arduino-lib-builder")
    checkout_submodule_version(SUBMODULES["arduino-esp32"], repo_versions["arduino"], repo_name="arduino-esp32")

    update_component_versions(SUBMODULES["esp-idf"], versions["components"])
    update_component_versions(SUBMODULES["arduino-esp32"], versions["components"])

    for repo_name, patch_dir_name in [("esp-idf", "esp-idf"), ("arduino-esp32", "arduino-esp32"), ("esp32-arduino-lib-builder", "esp32-arduino-lib-builder")]:
        patch_dir = PATCHES_ROOT / patch_dir_name
        apply_repo_patches(SUBMODULES[repo_name], patch_dir)

    build_target(args.target)


if __name__ == "__main__":
    main()
