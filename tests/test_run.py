import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run
from run import parse_versions, apply_repo_patches, checkout_submodule_version, build_target


class RunScriptTests(unittest.TestCase):
    def test_parse_versions_extracts_repo_and_component_versions(self):
        versions = """lib-builder: master 43a8f6d
esp-idf: v5.5.4 735507283d
arduino: master 6cb835025
espressif__cbor: 0.6.1~4
"""

        parsed = parse_versions(versions)

        self.assertEqual(parsed["repos"]["lib-builder"], ("master", "43a8f6d"))
        self.assertEqual(parsed["repos"]["esp-idf"], ("v5.5.4", "735507283d"))
        self.assertEqual(parsed["components"]["espressif__cbor"], "0.6.1~4")

    def test_apply_repo_patches_appends_and_copies_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            target_file = repo_dir / "README.md"
            target_file.write_text("hello\n", encoding="utf-8")
            patch_dir = repo_dir / "patches"
            patch_dir.mkdir()
            (patch_dir / "README.md.append").write_text("world\n", encoding="utf-8")
            (patch_dir / "config.txt").write_text("config\n", encoding="utf-8")

            apply_repo_patches(repo_dir, patch_dir)

            self.assertEqual(target_file.read_text(encoding="utf-8"), "hello\nworld\n")
            self.assertEqual((repo_dir / "config.txt").read_text(encoding="utf-8"), "config\n")

    def test_checkout_submodule_version_raises_for_missing_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (repo_dir / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            with self.assertRaises(RuntimeError):
                checkout_submodule_version(repo_dir, ("master", "deadbeef"))

    def test_checkout_submodule_version_skips_recursive_submodule_fetches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "branch", "-M", "master"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            remote_dir = repo_dir / "remote.git"
            subprocess.run(["git", "init", "--bare", str(remote_dir)], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", str(remote_dir)], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            (repo_dir / ".gitmodules").write_text("[submodule \"dummy\"]\n\tpath = submod\n\turl = https://example.invalid/nope\n", encoding="utf-8")
            (repo_dir / "submod").mkdir()
            (repo_dir / "submod" / ".keep").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "config", "submodule.recurse", "true"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "add", ".gitmodules", "submod"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "push", "-u", "origin", "master"], cwd=repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            checkout_submodule_version(repo_dir, ("master", ""))

    def test_build_target_passes_target_to_build_sh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder_dir = Path(tmpdir)
            submodules = dict(run.SUBMODULES)
            submodules["esp32-arduino-lib-builder"] = builder_dir
            submodules["esp-idf"] = builder_dir / "esp-idf"

            with mock.patch.object(run, "SUBMODULES", submodules), \
                 mock.patch.object(run, "ensure_system_dependencies"), \
                 mock.patch.object(run, "ensure_idf_environment"), \
                 mock.patch.object(run.subprocess, "run") as subprocess_run:
                build_target("esp32")

            subprocess_run.assert_called_once()
            self.assertEqual(subprocess_run.call_args.args[0], ["bash", "-c", 'source "$IDF_PATH/export.sh" >/dev/null && exec bash build.sh -t esp32 -s -e'])

    def test_build_target_without_target_lets_build_sh_build_all_envs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder_dir = Path(tmpdir)
            submodules = dict(run.SUBMODULES)
            submodules["esp32-arduino-lib-builder"] = builder_dir
            submodules["esp-idf"] = builder_dir / "esp-idf"

            with mock.patch.object(run, "SUBMODULES", submodules), \
                 mock.patch.object(run, "ensure_system_dependencies"), \
                 mock.patch.object(run, "ensure_idf_environment"), \
                 mock.patch.object(run.subprocess, "run") as subprocess_run:
                build_target()

            subprocess_run.assert_called_once()
            self.assertEqual(subprocess_run.call_args.args[0], ["bash", "-c", 'source "$IDF_PATH/export.sh" >/dev/null && exec bash build.sh -s -e'])


if __name__ == "__main__":
    unittest.main()
