from pathlib import Path
import subprocess
import sys
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
        self.assertIn("for attempt in $(seq 1 30)", self.content)
        self.assertIn("for attempt in $(seq 1 2)", self.content)

    def test_captures_android_diagnostics_even_on_early_failure(self):
        self.assertIn("trap collect_diagnostics EXIT", self.content)
        self.assertIn('adb logcat -d > "$OUT_DIR/android-logcat.txt"', self.content)
        self.assertIn('adb pull /sdcard/termux-mate-screen.png "$OUT_DIR/termux-mate-screen.png"', self.content)

    def test_pregrants_android_notification_permission_for_x11(self):
        self.assertIn("pm grant com.termux android.permission.POST_NOTIFICATIONS", self.content)
        self.assertIn("pm grant com.termux.x11 android.permission.POST_NOTIFICATIONS", self.content)
        self.assertIn("cmd appops set com.termux.x11 POST_NOTIFICATION allow", self.content)

    def test_clears_termux_exec_preload_before_starting_proot(self):
        self.assertIn("unset LD_PRELOAD", self.content)
        self.assertIn('nohup "$PREFIX/bin/proot" \\', self.content)
        self.assertIn("/usr/bin/env -i", self.content)

    def test_requires_the_mate_window_manager_for_success(self):
        self.assertIn('if [ "$session_ok" != true ] || [ "$panel_ok" != true ] || [ "$wm_ok" != true ]; then', self.content)


    def test_uses_adb_reverse_https_tunnel_and_official_cdn_sources(self):
        self.assertIn("adb reverse tcp:3128 tcp:3128", self.content)
        self.assertIn('Acquire::https::Proxy "http://127.0.0.1:3128";', self.content)
        self.assertIn("packages-cf.termux.dev/apt/termux-main", self.content)
        self.assertIn("apt-get install -y termux-x11-nightly", self.content)

    def test_captures_screenshot_before_asserting_it_exists(self):
        verification = self.content.index('::group::Verify Termux:X11 is the visible foreground activity')
        capture = self.content.index('adb shell screencap -p /sdcard/termux-mate-screen.png', verification)
        assertion = self.content.index('test -s "$OUT_DIR/termux-mate-screen.png"', capture)
        self.assertLess(capture, assertion)
        self.assertNotIn('Android screenshot was not produced', self.content)

    def test_proxy_server_compiles(self):
        proxy = ROOT / ".github/scripts/termux-ci-connect-proxy.py"
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(proxy)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_android_package_install_and_first_launch_are_bounded(self):
        self.assertIn('timeout --foreground --signal=TERM --kill-after=10s 120s \\', self.content)
        self.assertIn('timeout --foreground --signal=TERM --kill-after=3s 3s \\', self.content)
        self.assertIn('timeout --foreground --signal=TERM --kill-after=10s 60s adb push "$apk" "$remote_path"', self.content)
        self.assertIn('timeout --foreground --signal=TERM --kill-after=10s 30s adb shell monkey -p com.termux 1', self.content)
        self.assertIn('timeout --foreground --signal=TERM --kill-after=3s 3s \\', self.content)

    def test_guest_stages_have_hard_timeouts_and_named_progress(self):
        for label in (
            "Termux main repository connectivity",
            "Update Termux main indexes",
            "Install PRoot utilities and X11 repository",
            "Update main and X11 package indexes",
            "Install Termux:X11 package",
            "Extract Noble MATE rootfs",
        ):
            self.assertIn(label, self.content)
        self.assertIn('timeout --foreground --signal=TERM --kill-after=15s "${seconds}s" "$@"', self.content)
        self.assertIn('Acquire::Retries "1";', self.content)
        self.assertIn('Acquire::https::Timeout "20";', self.content)
        self.assertIn('run_phase "Termux main repository connectivity" 90', self.content)
        self.assertIn('run_phase "Install PRoot utilities and X11 repository" 360', self.content)
        self.assertIn('run_phase "Install Termux:X11 package" 480', self.content)
        self.assertIn('run_phase "Extract Noble MATE rootfs" 600', self.content)
        self.assertIn('timeout --foreground --signal=TERM --kill-after=30s 42m', self.content)
    def test_installs_both_termux_packages(self):
        self.assertIn('install_apk_without_streaming "$TERMUX_APK" /data/local/tmp/termux.apk com.termux', self.content)
        self.assertIn('install_apk_without_streaming "$X11_APK" /data/local/tmp/termux-x11.apk com.termux.x11', self.content)


if __name__ == "__main__":
    unittest.main()
