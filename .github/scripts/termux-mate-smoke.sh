#!/usr/bin/env bash
set -Eeuo pipefail

ROOTFS_ARCHIVE="${GITHUB_WORKSPACE:-$PWD}/fs-cook/out/noble-mateRaw-amd64.tar.gz"
OUT_DIR="${RUNNER_TEMP:-/tmp}/termux-mate-smoke"
mkdir -p "$OUT_DIR"
test -s "$ROOTFS_ARCHIVE"

echo "::group::Download official Termux and Termux:X11 APKs"
curl --fail --silent --show-error --location \
  "https://api.github.com/repos/termux/termux-app/releases?per_page=10" \
  -o "$OUT_DIR/termux-releases.json"
TERMUX_APK_URL="$(jq -r '[.[] | .assets[]? | select(.name | endswith("apt-android-7-github-debug_x86_64.apk"))][0].browser_download_url // empty' "$OUT_DIR/termux-releases.json")"
test -n "$TERMUX_APK_URL" || { echo "No x86_64 Android 7+ Termux APK asset found"; exit 1; }
curl --fail --location --retry 4 --retry-all-errors "$TERMUX_APK_URL" -o "$OUT_DIR/termux.apk"

curl --fail --silent --show-error --location \
  "https://api.github.com/repos/termux/termux-x11/releases/tags/nightly" \
  -o "$OUT_DIR/termux-x11-release.json"
X11_APK_URL="$(jq -r '.assets[] | select(.name == "termux-x11-universal-release.apk") | .browser_download_url' "$OUT_DIR/termux-x11-release.json" | head -n 1)"
test -n "$X11_APK_URL" || { echo "No Termux:X11 universal release APK found"; exit 1; }
curl --fail --location --retry 4 --retry-all-errors "$X11_APK_URL" -o "$OUT_DIR/termux-x11.apk"
echo "Termux APK: $TERMUX_APK_URL"
echo "Termux:X11 APK: $X11_APK_URL"
echo "::endgroup::"

echo "::group::Install Android applications"
adb wait-for-device
adb install -r "$OUT_DIR/termux.apk"
adb install -r "$OUT_DIR/termux-x11.apk"
adb shell settings put system screen_off_timeout 1800000
adb shell settings put global window_animation_scale 0
adb shell settings put global transition_animation_scale 0
adb shell settings put global animator_duration_scale 0
adb shell input keyevent 82 || true
adb shell monkey -p com.termux 1
echo "Waiting for Termux bootstrap..."
ready=false
for attempt in $(seq 1 90); do
  if adb shell run-as com.termux /system/bin/sh -c \
      'test -x /data/data/com.termux/files/usr/bin/bash' >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 3
done
if [ "$ready" != true ]; then
  adb logcat -d > "$OUT_DIR/android-logcat-bootstrap.txt" || true
  echo "Termux bootstrap did not create its bash executable"
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
nohup proot \
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
if [ "$session_ok" != true ] || [ "$panel_ok" != true ]; then
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
adb shell run-as com.termux /system/bin/sh -c \
  'cp /data/local/tmp/noble-mateRaw-amd64.tar.gz /data/data/com.termux/files/home/noble-mateRaw-amd64.tar.gz && cp /data/local/tmp/termux-guest-smoke.sh /data/data/com.termux/files/home/termux-guest-smoke.sh'
echo "::endgroup::"

echo "::group::Run MATE inside real Termux PRoot and X11"
set +e
adb shell run-as com.termux /system/bin/sh -c \
  'export HOME=/data/data/com.termux/files/home; export PREFIX=/data/data/com.termux/files/usr; export TMPDIR=$PREFIX/tmp; export LD_LIBRARY_PATH=$PREFIX/lib; export LD_PRELOAD=$PREFIX/lib/libtermux-exec.so; exec $PREFIX/bin/bash $HOME/termux-guest-smoke.sh' \
  2>&1 | tee "$OUT_DIR/termux-install-and-launch.log"
guest_status=${PIPESTATUS[0]}
set -e
echo "::endgroup::"

echo "::group::Collect Android, Termux, X11 and MATE diagnostics"
adb shell screencap -p /sdcard/termux-mate-screen.png >/dev/null 2>&1 || true
adb pull /sdcard/termux-mate-screen.png "$OUT_DIR/termux-mate-screen.png" >/dev/null 2>&1 || true
adb logcat -d > "$OUT_DIR/android-logcat.txt" || true
adb shell dumpsys window > "$OUT_DIR/android-window.txt" || true
adb shell run-as com.termux /system/bin/sh -c \
  'for f in mate-session.log termux-x11.log termux-x11-activity.log mate-processes.txt x11-sockets.txt; do if [ -f /data/data/com.termux/files/home/$f ]; then echo "===== $f ====="; cat /data/data/com.termux/files/home/$f; fi; done' \
  > "$OUT_DIR/termux-runtime-logs.txt" 2>&1 || true
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
