import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "suites/noble/mateRaw"


class NobleMateBuildTest(unittest.TestCase):
    def run_build(self, architecture="arm64"):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "plugins").mkdir()
            (work / "core/pv").mkdir(parents=True)
            (work / "core/pv/pv-arm64.deb").write_text("test package")
            for source in VARIANT.iterdir():
                if source.is_file():
                    shutil.copy2(source, work / source.name)
            record = work / "sources.list"
            calls = work / "calls.txt"
            (work / "plugins/envsetup").write_text(r"""
ROOT_DIR="$PWD"
SUDO=""
die() { echo "$*" >&2; exit 1; }
shout() { :; }
lshout() { :; }
run_cmd() { :; }
run_shell_script() {
    test -s "$1" || { echo "missing setup script: $1" >&2; exit 1; }
    printf '%s\n' "$1" >> "$CALLS"
}
do_build() {
    chroot_dir="$1"
    t_arch="$2"
    mkdir -p "$chroot_dir/etc/apt"
    additional_setup
    cp "$chroot_dir/etc/apt/sources.list" "$RECORD"
}
do_compress() { mkdir -p "$(dirname "$1")"; touch "$1.tar.gz"; }
do_unmount() { :; }
""")
            result = subprocess.run(
                ["bash", str(VARIANT / "mateRaw.sh"), architecture],
                cwd=work, capture_output=True, text=True,
                env={**os.environ, "RECORD": str(record), "CALLS": str(calls)},
            )
            sources = record.read_text() if record.exists() else ""
            invoked = calls.read_text().splitlines() if calls.exists() else []
            archives = [p.name for p in work.glob("out/*.tar.gz")]
            return result, sources, invoked, archives

    def test_noble_mate_build_sets_sources_and_packs_arm64(self):
        result, sources, invoked, archives = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("noble main restricted universe multiverse", sources)
        self.assertIn("noble-updates", sources)
        self.assertIn("noble-backports", sources)
        self.assertEqual(
            invoked,
            ["mateRaw-setup.sh", "mateRaw-fixes.sh", "mateRaw-cleanup.sh"],
        )
        self.assertEqual(archives, ["noble-mateRaw-arm64.tar.gz"])

    def test_rejects_unsupported_architectures_without_creating_archive(self):
        result, _, _, archives = self.run_build("amd64")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(archives, [])

    def test_matrix_contains_only_noble_mate_arm64(self):
        matrix = subprocess.check_output(
            [sys.executable, "gen_data/gen-suite-matrix.py"],
            cwd=ROOT, text=True,
        )
        rows = json.loads(matrix)["include"]
        matches = [
            row for row in rows
            if row["suite"] == "noble" and row["variant"] == "mateRaw"
        ]
        self.assertEqual(matches, [{
            "suite": "noble",
            "variant": "mateRaw",
            "architecture": "arm64",
        }])

    def test_setup_is_lightweight_and_uses_noble_mate_packages(self):
        setup = (VARIANT / "mateRaw-setup.sh").read_text()
        self.assertIn("mate-desktop-environment-core", setup)
        self.assertIn("ubuntu-mate-default-settings", setup)
        self.assertIn("ubuntu-mate-icon-themes", setup)
        self.assertIn("gvfs-backends", setup)
        self.assertNotIn("ubuntu-mate-desktop", setup)
        self.assertNotIn("lightdm", setup)
        self.assertNotIn("\n    xorg", setup)

    def test_launcher_checks_x11_and_uses_dbus_session(self):
        fixes = (VARIANT / "mateRaw-fixes.sh").read_text()
        self.assertIn("org.freedesktop.login1.service", fixes)
        self.assertIn('xdpyinfo -display "$DISPLAY"', fixes)
        self.assertIn("dbus-daemon --system --fork", fixes)
        self.assertIn("dbus-run-session -- mate-session", fixes)
        self.assertIn("UDROID_MATE_RUNTIME_DIR", fixes)

    def test_launcher_runs_mate_after_starting_the_system_bus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bindir = root / "bin"
            bindir.mkdir()
            state = root / "system-bus-ready"
            started = root / "mate-started"
            runtime = root / "runtime"

            scripts = {
                "xdpyinfo": "#!/bin/sh\nexit 0\n",
                "dbus-uuidgen": "#!/bin/sh\nexit 0\n",
                "dbus-send": (
                    "#!/bin/sh\n"
                    f"[ -f '{state}' ]\n"
                ),
                "dbus-daemon": (
                    "#!/bin/sh\n"
                    f"touch '{state}'\n"
                ),
                "rm": "#!/bin/sh\nexit 0\n",
                "dbus-run-session": "#!/bin/sh\n[ \"$1\" = -- ] && shift\nexec \"$@\"\n",
                "mate-session": (
                    "#!/bin/sh\n"
                    f"printf '%s\\n' \"$DISPLAY|$XDG_CURRENT_DESKTOP|$XDG_SESSION_TYPE\" > '{started}'\n"
                ),
            }
            for name, script in scripts.items():
                path = bindir / name
                path.write_text(script)
                path.chmod(0o755)

            fixes = (VARIANT / "mateRaw-fixes.sh").read_text()
            payload = fixes.split(
                "cat > /usr/local/bin/udroid-mate-x11 <<'EOF_LAUNCHER'\n", 1
            )[1].split("\nEOF_LAUNCHER", 1)[0]
            launch_path = root / "udroid-mate-x11"
            launch_path.write_text(payload)
            launch_path.chmod(0o755)

            result = subprocess.run(
                ["bash", str(launch_path)], capture_output=True, text=True,
                env={
                    **os.environ,
                    "PATH": f"{bindir}:{os.environ['PATH']}",
                    "DISPLAY": ":0",
                    "UDROID_MATE_RUNTIME_DIR": str(runtime),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(state.exists(), "system D-Bus was not started")
            self.assertEqual(started.read_text().strip(), ":0|MATE|x11")
            self.assertTrue(runtime.is_dir())
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)

    def test_shell_scripts_parse(self):
        for path in sorted(VARIANT.glob("*.sh")):
            result = subprocess.run(
                ["bash", "-n", str(path)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, f"{path}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
