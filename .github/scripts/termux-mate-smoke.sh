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
  adb logcat -d > "$OUT_DIR/android-logcat.txt" 2>&1
  adb shell getprop > "$OUT_DIR/android-getprop.txt" 2>&1
  adb shell dumpsys window > "$OUT_DIR/android-window.txt" 2>&1
  adb shell dumpsys package > "$OUT_DIR/android-packages.txt" 2>&1
  adb shell screencap -p /sdcard/termux-mate-screen.png >/dev/null 2>&1
  adb pull /sdcard/termux-mate-screen.png "$OUT_DIR/termux-mate-screen.png" >/dev/null 2>&1
  adb shell run-as com.termux /system/bin/ls -l "$TERMUX_PREFIX/bin/bash" \
    > "$OUT_DIR/termux-bash-file.txt" 2>&1
  : > "$OUT_DIR/termux-runtime-logs.txt"
  for f in mate-session.log termux-x11.log termux-x11-activity.log mate-processes.txt x11-sockets.txt; do
    echo "===== $f =====" >> "$OUT_DIR/termux-runtime-logs.txt"
    adb shell run-as com.termux /system/bin/cat "$TERMUX_HOME/$f" \
      >> "$OUT_DIR/termux-runtime-logs.txt" 2>&1 || true
  done
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
  for attempt in $(seq 1 45); do
    if adb shell pm list packages >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Android package manager did not become ready" >&2
  return 1
}

install_apk_without_streaming() {
  local apk="$1"
  local remote_path="$2"
  local package_name="$3"

  echo "Pushing $apk to Android filesystem"
  adb push "$apk" "$remote_path"

  for attempt in $(seq 1 4); do
    if adb shell pm path "$package_name" 2>/dev/null | grep -q '^package:'; then
      echo "$package_name is already installed"
      return 0
    fi

    echo "Installing $package_name with local pm install (attempt $attempt/4)"
    if adb shell pm install -r "$remote_path"; then
      if adb shell pm path "$package_name" 2>/dev/null | grep -q '^package:'; then
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
adb shell monkey -p com.termux 1
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
mkdir -p "$HOME" "$TMPDIR"

log() { printf '[termux-mate-smoke] %s\n' "$*"; }

log "Updating Termux repositories"
pkg update -y
log "Installing Termux PRoot and utilities"
pkg install -y proot tar procps
log "Enabling the official Termux X11 repository"
pkg install -y x11-repo
pkg update -y
log "Installing the Termux:X11 companion package"
pkg install -y termux-x11-nightly

ARCHIVE="$HOME/noble-mateRaw-amd64.tar.gz"
ROOTFS="$HOME/noble-mate-amd64"
test -s "$ARCHIVE"
rm -rf "$ROOTFS"
mkdir -p "$ROOTFS"
log "Extracting the built Noble MATE rootfs"
tar -xzf "$ARCHIVE" -C "$ROOTFS"
test -x "$ROOTFS/usr/bin/mate-session"
test -x "$ROOTFS/usr/bin/mate-panel"
test -x "$ROOTFS/usr/local/bin/udroid-mate-x11"

log "Starting the real Termux:X11 server on display :0"
TERMUX_X11_DEBUG=1 nohup termux-x11 :0 \
  > "$HOME/termux-x11.log" 2>&1 < /dev/null &
echo "$!" > "$HOME/termux-x11.pid"
sleep 4
am start --user 0 -n com.termux.x11/com.termux.x11.MainActivity \
  > "$HOME/termux-x11-activity.log" 2>&1
sleep 3

log "Checking the Termux:X11 socket"
if [ ! -S "$TMPDIR/.X11-unix/X0" ]; then
  find "$TMPDIR" -maxdepth 4 -type s -print > "$HOME/x11-sockets.txt" || true
  log "The expected X11 socket was not created at $TMPDIR/.X11-unix/X0"
  cat "$HOME/termux-x11.log" >&2 || true
  exit 20
fi

log "Launching MATE inside the Noble rootfs using Termux's own proot"
# termux-exec rewrites absolute executable paths for Android. That behavior
# is invalid inside PRoot and makes /usr/bin/env resolve to a Termux path.
# Keep Termux's LD_LIBRARY_PATH for the host proot binary, but remove the
# preload before PRoot starts so guest executable paths stay guest-relative.
unset LD_PRELOAD
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
for attempt in $(seq 1 90); do
  pgrep -f '[m]ate-session' >/dev/null 2>&1 && session_ok=true
  pgrep -f '[m]ate-panel' >/dev/null 2>&1 && panel_ok=true
  pgrep -f '[m]arco' >/dev/null 2>&1 && wm_ok=true
  if [ "$session_ok" = true ] && [ "$panel_ok" = true ] && [ "$wm_ok" = true ]; then
    break
  fi
  sleep 2
done

log "MATE process check: session=$session_ok panel=$panel_ok marco=$wm_ok"
pgrep -af 'mate-session|mate-panel|marco|caja' > "$HOME/mate-processes.txt" || true
if [ "$session_ok" != true ] || [ "$panel_ok" != true ] || [ "$wm_ok" != true ]; then
  cat "$HOME/mate-session.log" >&2 || true
  cat "$HOME/termux-x11.log" >&2 || true
  exit 21
fi

sleep 8
log "Termux/PRoot MATE smoke test passed"
GUEST

echo "::group::Copy rootfs and test script into Termux app storage"
adb shell chmod 755 /data/local/tmp
adb push "$ROOTFS_ARCHIVE" /data/local/tmp/noble-mateRaw-amd64.tar.gz
adb push "$OUT_DIR/termux-guest-smoke.sh" /data/local/tmp/termux-guest-smoke.sh
adb shell chmod 644 /data/local/tmp/noble-mateRaw-amd64.tar.gz
adb shell chmod 644 /data/local/tmp/termux-guest-smoke.sh
adb shell run-as com.termux /system/bin/cp \
  /data/local/tmp/noble-mateRaw-amd64.tar.gz "$TERMUX_HOME/noble-mateRaw-amd64.tar.gz"
adb shell run-as com.termux /system/bin/cp \
  /data/local/tmp/termux-guest-smoke.sh "$TERMUX_HOME/termux-guest-smoke.sh"
echo "::endgroup::"

echo "::group::Run MATE inside real Termux PRoot and X11"
set +e
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

test -s "$OUT_DIR/termux-mate-screen.png" || {
  echo "Android screenshot was not produced"
  exit 22
}

echo "Termux MATE integration smoke test completed"
