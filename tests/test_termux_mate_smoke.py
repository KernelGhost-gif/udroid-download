from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / ".github/scripts/termux-mate-smoke.sh"


class TermuxMateSmokeScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = SMOKE.read_text()

    def test_smoke_script_has_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SMOKE)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_predownloaded_apks_instead_of_github_api_at_runtime(self):
        self.assertIn('TERMUX_APK="${RUNNER_TEMP:-/tmp}/termux-mate-apks/termux.apk"', self.content)
        self.assertIn('X11_APK="${RUNNER_TEMP:-/tmp}/termux-mate-apks/termux-x11.apk"', self.content)
        self.assertNotIn("api.github.com/repos/termux", self.content)

    def test_uses_push_and_local_package_manager_install(self):
        self.assertIn('adb push "$apk" "$remote_path"', self.content)
        self.assertIn('adb shell pm install -r "$remote_path"', self.content)
        self.assertNotIn('adb install -r "$TERMUX_APK"', self.content)
        self.assertNotIn('adb install -r "$X11_APK"', self.content)

    def test_waits_for_the_real_termux_bootstrap_completion_marker(self):
        self.assertIn("Termux.TermuxInstaller:I", self.content)
        self.assertIn("Bootstrap packages installed successfully", self.content)
        self.assertIn('adb shell run-as com.termux /system/bin/ls -l "$TERMUX_PREFIX/bin/bash"', self.content)

    def test_avoids_nested_remote_shell_quoting_for_run_as(self):
        self.assertIn("adb shell run-as com.termux /system/bin/cp", self.content)
        self.assertIn("adb shell run-as com.termux /system/bin/env", self.content)
        self.assertNotIn("run-as com.termux /system/bin/sh -c", self.content)

    def test_retries_when_android_package_manager_is_not_ready(self):
        self.assertIn("wait_for_package_manager()", self.content)
        self.assertIn("for attempt in $(seq 1 45)", self.content)
        self.assertIn("for attempt in $(seq 1 4)", self.content)

    def test_captures_android_diagnostics_even_on_early_failure(self):
        self.assertIn("trap collect_diagnostics EXIT", self.content)
        self.assertIn('adb logcat -d > "$OUT_DIR/android-logcat.txt"', self.content)
        self.assertIn('adb pull /sdcard/termux-mate-screen.png "$OUT_DIR/termux-mate-screen.png"', self.content)

    def test_clears_termux_exec_preload_before_starting_proot(self):
        self.assertIn("unset LD_PRELOAD", self.content)
        self.assertIn('nohup "$PREFIX/bin/proot" \\', self.content)
        self.assertIn("/usr/bin/env -i", self.content)

    def test_installs_both_termux_packages(self):
        self.assertIn('install_apk_without_streaming "$TERMUX_APK" /data/local/tmp/termux.apk com.termux', self.content)
        self.assertIn('install_apk_without_streaming "$X11_APK" /data/local/tmp/termux-x11.apk com.termux.x11', self.content)


if __name__ == "__main__":
    unittest.main()
