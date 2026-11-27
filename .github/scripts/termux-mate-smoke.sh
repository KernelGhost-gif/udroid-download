#!/usr/bin/env bash
set -Eeuo pipefail

ROOTFS_ARCHIVE="${GITHUB_WORKSPACE:-$PWD}/fs-cook/out/noble-mateRaw-amd64.tar.gz"
OUT_DIR="${RUNNER_TEMP:-/tmp}/termux-mate-smoke"
TERMUX_HOME=/data/data/com.termux/files/home
TERMUX_PREFIX=/data/data/com.termux/files/usr
mkdir -p "$OUT_DIR"
test -s "$ROOTFS_ARCHIVE"

collect_diagnostics() {
  local status=$?
  trap - EXIT
  set +e
  adb devices -l > "$OUT_DIR/adb-devices.txt" 2>&1
  adb reverse --list > "$OUT_DIR/adb-reverse.txt" 2>&1
  adb shell dumpsys connectivity > "$OUT_DIR/android-connectivity.txt" 2>&1
  adb shell ip route > "$OUT_DIR/android-ip-route.txt" 2>&1
  adb logcat -d > "$OUT_DIR/android-logcat.txt" 2>&1
  adb shell getprop > "$OUT_DIR/android-getprop.txt" 2>&1
  adb shell dumpsys window > "$OUT_DIR/android-window.txt" 2>&1
  adb shell dumpsys package > "$OUT_DIR/android-packages.txt" 2>&1
  adb shell screencap -p /sdcard/termux-mate-screen.png >/dev/null 2>&1
  adb pull /sdcard/termux-mate-screen.png "$OUT_DIR/termux-mate-screen.png" >/dev/null 2>&1
  adb shell run-as com.termux /system/bin/ls -l "$TERMUX_PREFIX/bin/bash" \
    > "$OUT_DIR/termux-bash-file.txt" 2>&1
  : > "$OUT_DIR/termux-runtime-logs.txt"
  for f in mate-session.log caja-desktop.log termux-x11.log termux-x11-activity.log mate-processes.txt x11-sockets.txt termux-main-InRelease; do
    echo "===== $f =====" >> "$OUT_DIR/termux-runtime-logs.txt"
    adb shell run-as com.termux /system/bin/cat "$TERMUX_HOME/$f" \
      >> "$OUT_DIR/termux-runtime-logs.txt" 2>&1 || true
  done
  if [ -n "${proxy_pid:-}" ]; then
    kill "$proxy_pid" 2>/dev/null || true
  fi
  exit "$status"
}
trap collect_diagnostics EXIT

echo "::group::Check predownloaded official Termux and Termux:X11 APKs"
TERMUX_APK="${RUNNER_TEMP:-/tmp}/termux-mate-apks/termux.apk"
X11_APK="${RUNNER_TEMP:-/tmp}/termux-mate-apks/termux-x11.apk"
test -s "$TERMUX_APK" || { echo "Termux APK missing: $TERMUX_APK"; exit 1; }
test -s "$X11_APK" || { echo "Termux:X11 APK missing: $X11_APK"; exit 1; }
sha256sum "$TERMUX_APK" "$X11_APK"
echo "Termux APK ready: $TERMUX_APK"
echo "Termux:X11 APK ready: $X11_APK"
echo "::endgroup::"

wait_for_package_manager() {
  for attempt in $(seq 1 30); do
    if timeout --foreground --signal=TERM --kill-after=3s 3s \
        adb shell pm list packages >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Android package manager did not become ready" >&2
  return 1
}

install_apk_without_streaming() {
  local apk="$1"
  local remote_path="$2"
  local package_name="$3"

  echo "Pushing $apk to Android filesystem"
  timeout --foreground --signal=TERM --kill-after=10s 60s adb push "$apk" "$remote_path"

  for attempt in $(seq 1 2); do
    if timeout --foreground --signal=TERM --kill-after=3s 10s \
        adb shell pm path "$package_name" 2>/dev/null | grep -q '^package:'; then
      echo "$package_name is already installed"
      return 0
    fi

    echo "Installing $package_name with local pm install (attempt $attempt/2)"
    if timeout --foreground --signal=TERM --kill-after=10s 120s \
        adb shell pm install -r "$remote_path"; then
      if timeout --foreground --signal=TERM --kill-after=3s 10s adb shell pm path "$package_name" 2>/dev/null | grep -q '^package:'; then
        echo "$package_name installed successfully"
        return 0
      fi
    fi

    echo "Package installation attempt $attempt failed; waiting for Android package manager"
    sleep 5
    wait_for_package_manager
  done

  echo "Failed to install $package_name" >&2
  adb logcat -d -t 1000 >&2 || true
  return 1
}

echo "::group::Install Android applications"
adb wait-for-device
wait_for_package_manager

echo "::group::Start host-side HTTPS proxy and reverse it into Android"
python3 "$GITHUB_WORKSPACE/.github/scripts/termux-ci-connect-proxy.py" \
  > "$OUT_DIR/termux-ci-proxy.log" 2>&1 &
proxy_pid=$!
echo "$proxy_pid" > "$OUT_DIR/termux-ci-proxy.pid"
proxy_ready=false
for attempt in $(seq 1 30); do
  if python3 -c 'import socket; s=socket.create_connection(("127.0.0.1", 3128), 1); s.close()' 2>/dev/null; then
    proxy_ready=true
    break
  fi
  sleep 1
done
if [ "$proxy_ready" != true ]; then
  cat "$OUT_DIR/termux-ci-proxy.log" >&2 || true
  echo "Host HTTPS proxy did not start" >&2
  exit 30
fi
curl --fail --silent --show-error --location \
  --proxy http://127.0.0.1:3128 --noproxy "" \
  --connect-timeout 15 --max-time 60 \
  https://packages-cf.termux.dev/apt/termux-main/dists/stable/InRelease \
  --output "$OUT_DIR/termux-main-InRelease"
test -s "$OUT_DIR/termux-main-InRelease"
adb reverse tcp:3128 tcp:3128
adb reverse --list | tee "$OUT_DIR/adb-reverse-ready.txt"
echo "Termux HTTPS repository reachable through the host tunnel"
echo "::endgroup::"

install_apk_without_streaming "$TERMUX_APK" /data/local/tmp/termux.apk com.termux
install_apk_without_streaming "$X11_APK" /data/local/tmp/termux-x11.apk com.termux.x11
# Avoid a first-launch Android notification prompt interrupting Termux:X11.
adb shell pm grant com.termux android.permission.POST_NOTIFICATIONS 2>/dev/null || true
adb shell pm grant com.termux.x11 android.permission.POST_NOTIFICATIONS 2>/dev/null || true
adb shell cmd appops set com.termux POST_NOTIFICATION allow 2>/dev/null || true
adb shell cmd appops set com.termux.x11 POST_NOTIFICATION allow 2>/dev/null || true
adb shell settings put system screen_off_timeout 1800000
adb shell settings put global window_animation_scale 0
adb shell settings put global transition_animation_scale 0
adb shell settings put global animator_duration_scale 0
adb shell input keyevent 82 || true
timeout --foreground --signal=TERM --kill-after=10s 30s adb shell monkey -p com.termux 1
echo "Waiting for Termux's second-stage bootstrap completion marker..."
ready=false
for attempt in $(seq 1 100); do
  if adb logcat -d -s Termux.TermuxInstaller:I 2>/dev/null | \
      grep -Fq "Bootstrap packages installed successfully"; then
    ready=true
    break
  fi
  sleep 5
done
if [ "$ready" != true ]; then
  adb logcat -d > "$OUT_DIR/android-logcat-bootstrap.txt" || true
  echo "Termux bootstrap did not report successful completion"
  exit 1
fi
if ! adb shell run-as com.termux /system/bin/ls -l "$TERMUX_PREFIX/bin/bash" \
    > "$OUT_DIR/termux-bash-file.txt" 2>&1; then
  echo "Termux bootstrap marker appeared but bash is not accessible via run-as"
  cat "$OUT_DIR/termux-bash-file.txt" >&2 || true
  exit 1
fi
echo "Termux bootstrap is ready"
echo "::endgroup::"

cat > "$OUT_DIR/termux-guest-smoke.sh" <<'GUEST'
#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
export TMPDIR="$PREFIX/tmp"
export PATH="$PREFIX/bin:/system/bin:/system/xbin"
export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_PRELOAD="$PREFIX/lib/libtermux-exec.so${LD_PRELOAD:+:$LD_PRELOAD}"
export TERM=xterm-256color
export http_proxy=http://127.0.0.1:3128
export https_proxy=http://127.0.0.1:3128
export HTTP_PROXY="$https_proxy"
export HTTPS_PROXY="$https_proxy"
export NO_PROXY=localhost,127.0.0.1,::1
mkdir -p "$HOME" "$TMPDIR"

# Use the official Cloudflare-cached Termux mirrors directly. This avoids
# probing dozens of mirrors from an emulator whose own outbound networking
# may be disabled; all HTTPS traffic is carried over the ADB reverse tunnel.
mkdir -p "$PREFIX/etc/apt/apt.conf.d"
cat > "$PREFIX/etc/apt/apt.conf.d/99-ci-proxy" <<'APT_PROXY'
Acquire::http::Proxy "http://127.0.0.1:3128";
Acquire::https::Proxy "http://127.0.0.1:3128";
Acquire::Retries "1";
Acquire::http::Timeout "20";
Acquire::https::Timeout "20";
APT_PROXY
cat > "$PREFIX/etc/apt/sources.list" <<'TERMUX_MAIN'
deb https://packages-cf.termux.dev/apt/termux-main stable main
TERMUX_MAIN

log() { printf '[termux-mate-smoke] %s\n' "$*"; }

run_phase() {
  local label="$1"
  local seconds="$2"
  shift 2
  log "BEGIN: $label (hard limit ${seconds}s)"
  if timeout --foreground --signal=TERM --kill-after=15s "${seconds}s" "$@"; then
    log "PASS: $label"
  else
    local status=$?
    log "FAIL: $label (exit $status)"
    return "$status"
  fi
}

run_phase "Termux main repository connectivity" 90 \
  curl --fail --silent --show-error --location \
    --connect-timeout 15 --max-time 90 \
    https://packages-cf.termux.dev/apt/termux-main/dists/stable/InRelease \
    --output "$HOME/termux-main-InRelease"
test -s "$HOME/termux-main-InRelease"

run_phase "Update Termux main indexes" 180 apt-get update
run_phase "Install PRoot utilities and X11 repository" 360 \
  apt-get install -y proot tar procps x11-repo
log "Pinning X11 packages to the official Cloudflare mirror"
if [ -d "$PREFIX/etc/apt/sources.list.d" ]; then
  find "$PREFIX/etc/apt/sources.list.d" -type f \
    \( -name '*.list' -o -name '*.sources' \) -print0 |
    xargs -0 -r sed -i 's#packages[.]termux[.]dev#packages-cf.termux.dev#g'
fi
run_phase "Update main and X11 package indexes" 180 apt-get update
run_phase "Install Termux:X11 package" 480 apt-get install -y termux-x11-nightly
command -v proot >/dev/null
command -v termux-x11 >/dev/null

ARCHIVE="$HOME/noble-mateRaw-amd64.tar.gz"
ROOTFS="$HOME/noble-mate-amd64"
test -s "$ARCHIVE"
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"
run_phase "Extract Noble MATE rootfs" 600 tar -xzf "$ARCHIVE" -C "$ROOTFS"
test -x "$ROOTFS/usr/bin/mate-session"
test -x "$ROOTFS/usr/bin/mate-panel"
test -x "$ROOTFS/usr/bin/caja"
test -s "$ROOTFS/usr/share/backgrounds/ubuntu-mate-common/Green-Wall.png"
test -x "$ROOTFS/usr/local/bin/udroid-mate-x11"

# termux-exec rewrites absolute executable paths for Android; that behavior
# breaks guest executable lookup in PRoot. Clear it before every guest PRoot
# command, including the package metadata check below.
unset LD_PRELOAD
for service in \
  org.mate.panel.applet.IndicatorAppletCompleteFactory.service \
  org.mate.panel.applet.BriskMenuFactory.service
do
  test -f "$ROOTFS/usr/share/dbus-1/services/$service" || {
    log "Required MATE panel D-Bus factory is missing: $service"
    exit 26
  }
done


log "Starting the real Termux:X11 server on display :0"
TERMUX_X11_DEBUG=1 nohup termux-x11 :0 \
  > "$HOME/termux-x11.log" 2>&1 < /dev/null &
echo "$!" > "$HOME/termux-x11.pid"
sleep 4
run_phase "Open Termux:X11 activity" 30 \
  am start --user 0 -n com.termux.x11/com.termux.x11.MainActivity \
    > "$HOME/termux-x11-activity.log" 2>&1
log "Waiting up to 45 seconds for the Termux:X11 socket"
socket_ready=false
for attempt in $(seq 1 45); do
  if [ -S "$TMPDIR/.X11-unix/X0" ]; then
    socket_ready=true
    break
  fi
  sleep 1
done
if [ "$socket_ready" != true ]; then
  find "$TMPDIR" -maxdepth 4 -type s -print > "$HOME/x11-sockets.txt" || true
  log "The expected X11 socket was not created at $TMPDIR/.X11-unix/X0"
  cat "$HOME/termux-x11.log" >&2 || true
  exit 20
fi
log "PASS: Termux:X11 socket is ready"

log "Launching MATE inside the Noble rootfs using Termux's own proot"
# Keep Termux's LD_LIBRARY_PATH for the host proot binary, but do not
# re-enable termux-exec while guest executables are starting.
nohup "$PREFIX/bin/proot" \
  --link2symlink \
  --sysvipc \
  -0 \
  -r "$ROOTFS" \
  -b /dev \
  -b /proc \
  -b /sys \
  -b "$TMPDIR:/tmp" \
  -b /dev/urandom:/dev/random \
  -w /root \
  /usr/bin/env -i \
    HOME=/root \
    USER=root \
    LOGNAME=root \
    TERM=xterm-256color \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DISPLAY=:0 \
    XDG_SESSION_TYPE=x11 \
    XDG_CURRENT_DESKTOP=MATE \
    XDG_SESSION_DESKTOP=mate \
    DESKTOP_SESSION=mate \
    GDK_BACKEND=x11 \
    XKB_CONFIG_ROOT=/usr/share/X11/xkb \
    UDROID_MATE_RUNTIME_DIR=/tmp/mate-runtime-termux-ci \
  /usr/local/bin/udroid-mate-x11 \
  > "$HOME/mate-session.log" 2>&1 < /dev/null &
echo "$!" > "$HOME/mate-proot.pid"

log "Waiting for MATE session, panel, and window manager"
session_ok=false
panel_ok=false
wm_ok=false
caja_ok=false
for attempt in $(seq 1 90); do
  pgrep -f '[m]ate-session' >/dev/null 2>&1 && session_ok=true
  pgrep -f '[m]ate-panel' >/dev/null 2>&1 && panel_ok=true
  pgrep -f '[m]arco' >/dev/null 2>&1 && wm_ok=true
  pgrep -x caja >/dev/null 2>&1 && caja_ok=true
  if [ "$session_ok" = true ] && [ "$panel_ok" = true ] && [ "$wm_ok" = true ] && [ "$caja_ok" = true ]; then
    break
  fi
  sleep 2
done

log "MATE process check: session=$session_ok panel=$panel_ok marco=$wm_ok caja_desktop=$caja_ok"
pgrep -af 'mate-session|mate-panel|marco|caja|mate-settings-daemon' > "$HOME/mate-processes.txt" || true
if [ -s "$TMPDIR/mate-runtime-termux-ci/caja-desktop.log" ]; then
  cp "$TMPDIR/mate-runtime-termux-ci/caja-desktop.log" "$HOME/caja-desktop.log"
fi
if [ "$session_ok" != true ] || [ "$panel_ok" != true ] || [ "$wm_ok" != true ] || [ "$caja_ok" != true ]; then
  cat "$HOME/mate-session.log" >&2 || true
  cat "$HOME/termux-x11.log" >&2 || true
  exit 21
fi

sleep 12
# Verify each configured panel applet package is installed.
for package in mate-indicator-applet mate-applet-brisk-menu; do
  run_phase "Verify configured MATE panel package: $package" 30 \
    "$PREFIX/bin/proot" --link2symlink -0 -r "$ROOTFS" \
      /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
      /usr/bin/dpkg-query -W -f='${Status}\n' "$package"
  if ! "$PREFIX/bin/proot" --link2symlink -0 -r "$ROOTFS" \
      /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
      /usr/bin/dpkg-query -W -f='${Status}\n' "$package" | \
      grep -Fq 'install ok installed'; then
    log "Required MATE panel package is not installed: $package"
    exit 24
  fi
done

# Verify the panel activated both configured applet factories; the desktop
# process check alone would miss a panel dialog caused by a missing factory.
for factory in IndicatorAppletCompleteFactory BriskMenuFactory; do
  if ! grep -Fq "Successfully activated service 'org.mate.panel.applet.$factory'" "$HOME/mate-session.log"; then
    log "MATE panel did not successfully activate $factory"
    grep -E "org.mate.panel.applet.$factory|BriskMenu|IndicatorApplet" "$HOME/mate-session.log" >&2 || true
    exit 28
  fi
done

log "Termux/PRoot MATE smoke test passed"
GUEST

echo "::group::Copy rootfs and test script into Termux app storage"
adb shell chmod 755 /data/local/tmp
timeout --foreground --signal=TERM --kill-after=15s 600s \
  adb push "$ROOTFS_ARCHIVE" /data/local/tmp/noble-mateRaw-amd64.tar.gz
timeout --foreground --signal=TERM --kill-after=15s 60s \
  adb push "$OUT_DIR/termux-guest-smoke.sh" /data/local/tmp/termux-guest-smoke.sh
adb shell chmod 644 /data/local/tmp/noble-mateRaw-amd64.tar.gz
adb shell chmod 644 /data/local/tmp/termux-guest-smoke.sh
timeout --foreground --signal=TERM --kill-after=15s 600s \
  adb shell run-as com.termux /system/bin/cp \
    /data/local/tmp/noble-mateRaw-amd64.tar.gz "$TERMUX_HOME/noble-mateRaw-amd64.tar.gz"
timeout --foreground --signal=TERM --kill-after=15s 60s \
  adb shell run-as com.termux /system/bin/cp \
    /data/local/tmp/termux-guest-smoke.sh "$TERMUX_HOME/termux-guest-smoke.sh"
echo "::endgroup::"

echo "::group::Run MATE inside real Termux PRoot and X11"
set +e
timeout --foreground --signal=TERM --kill-after=30s 42m \
  adb shell run-as com.termux /system/bin/env \
  "HOME=$TERMUX_HOME" \
  "PREFIX=$TERMUX_PREFIX" \
  "TMPDIR=$TERMUX_PREFIX/tmp" \
  "PATH=$TERMUX_PREFIX/bin:/system/bin:/system/xbin" \
  "LD_LIBRARY_PATH=$TERMUX_PREFIX/lib" \
  "LD_PRELOAD=$TERMUX_PREFIX/lib/libtermux-exec.so" \
  "TERM=xterm-256color" \
  "$TERMUX_PREFIX/bin/bash" "$TERMUX_HOME/termux-guest-smoke.sh" \
  2>&1 | tee "$OUT_DIR/termux-install-and-launch.log"
guest_status=${PIPESTATUS[0]}
set -e
echo "::endgroup::"

if [ "$guest_status" -ne 0 ]; then
  echo "Termux/PRoot/X11 smoke test failed with status $guest_status"
  exit "$guest_status"
fi

echo "::group::Recover and verify Android foreground state"
# A constrained software-rendered emulator can briefly stall System UI while
# MATE starts. Give the UI one recovery window, then fail if its ANR dialog
# remains; a process-only pass is not sufficient desktop validation.
adb shell dumpsys window > "$OUT_DIR/android-window-before-recovery.txt"
if grep -q "Application Not Responding: com.android.systemui" "$OUT_DIR/android-window-before-recovery.txt"; then
  adb shell input tap 280 1010 || true
  sleep 15
fi
adb shell dumpsys window > "$OUT_DIR/android-window-after-launch.txt"
if grep -q "Application Not Responding:" "$OUT_DIR/android-window-after-launch.txt"; then
  echo "Android still has an Application Not Responding dialog after the recovery window" >&2
  grep -E "Application Not Responding|mCurrentFocus|mFocusedApp" \
    "$OUT_DIR/android-window-after-launch.txt" >&2 || true
  exit 25
fi
if ! grep -q "com.termux.x11" "$OUT_DIR/android-window-after-launch.txt"; then
  echo "Termux:X11 is not the visible foreground activity after MATE launch" >&2
  grep -E "mCurrentFocus|mFocusedApp" "$OUT_DIR/android-window-after-launch.txt" >&2 || true
  exit 23
fi
echo "::endgroup::"

adb shell screencap -p /sdcard/termux-mate-screen.png
adb pull /sdcard/termux-mate-screen.png "$OUT_DIR/termux-mate-screen.png"
test -s "$OUT_DIR/termux-mate-screen.png"
/usr/bin/python3 "$GITHUB_WORKSPACE/.github/scripts/check-mate-screenshot.py" "$OUT_DIR/termux-mate-screen.png"
echo "::endgroup::"

echo "Termux MATE integration smoke test completed"
