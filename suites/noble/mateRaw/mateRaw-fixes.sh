#!/usr/bin/env bash
# Configure MATE to run as a desktop session inside Android PRoot.
set -Eeuo pipefail

# PRoot does not run systemd/logind. Keep the D-Bus activation definition for
# reference, but prevent clients from trying to activate the unavailable
# systemd-logind service.
login1_service=/usr/share/dbus-1/system-services/org.freedesktop.login1.service
if [[ -e "$login1_service" || -L "$login1_service" ]]; then
    install -d /root/login1-disabled
    mv -f "$login1_service" /root/login1-disabled/
fi

install -d /etc/profile.d
cat > /etc/profile.d/mate-termux-x11.sh <<'EOF_PROFILE'
export DISPLAY="${DISPLAY:-:0}"
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=MATE
export XDG_SESSION_DESKTOP=mate
export DESKTOP_SESSION=mate
export GDK_BACKEND=x11
export XKB_CONFIG_ROOT=/usr/share/X11/xkb
export GSK_RENDERER="${GSK_RENDERER:-cairo}"
EOF_PROFILE
chmod 0644 /etc/profile.d/mate-termux-x11.sh

cat > /usr/local/bin/udroid-mate-x11 <<'EOF_LAUNCHER'
#!/usr/bin/env bash
set -Eeuo pipefail

die() {
    printf '[udroid-mate] ERROR: %s\n' "$*" >&2
    exit 1
}

# Do not reuse a stale desktop or D-Bus session inherited from another
# terminal. A fresh session bus is created below for mate-session.
unset SESSION_MANAGER DBUS_SESSION_BUS_ADDRESS DBUS_SYSTEM_BUS_ADDRESS

export DISPLAY="${DISPLAY:-:0}"
export XDG_SESSION_TYPE=x11
export XDG_CURRENT_DESKTOP=MATE
export XDG_SESSION_DESKTOP=mate
export DESKTOP_SESSION=mate
export GDK_BACKEND=x11
export GSK_RENDERER="${GSK_RENDERER:-cairo}"

# Android PRoot has no logind-managed /run/user/<uid>. Use a private,
# writable runtime directory instead.
export XDG_RUNTIME_DIR="${UDROID_MATE_RUNTIME_DIR:-/tmp/mate-runtime-$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR" || die "cannot create $XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR" || die "cannot secure $XDG_RUNTIME_DIR"

command -v mate-session >/dev/null 2>&1 || die "mate-session is not installed"
command -v dbus-send >/dev/null 2>&1 || die "dbus-send is not installed"
command -v dbus-daemon >/dev/null 2>&1 || die "dbus-daemon is not installed"
command -v dbus-run-session >/dev/null 2>&1 || die "dbus-run-session is not installed"

if command -v xdpyinfo >/dev/null 2>&1 &&
   ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    die "cannot connect to X server at DISPLAY=$DISPLAY; start Termux:X11 first"
fi

# A valid machine ID is required by the system bus.
if command -v dbus-uuidgen >/dev/null 2>&1; then
    dbus-uuidgen --ensure=/etc/machine-id ||
        die "could not create /etc/machine-id"
fi

# Start a system bus only when one is not already responding. This keeps
# repeated launcher invocations from killing a working bus.
if ! dbus-send --system --print-reply \
    --dest=org.freedesktop.DBus / org.freedesktop.DBus.ListNames \
    >/dev/null 2>&1; then
    mkdir -p /run/dbus
    rm -f /run/dbus/pid /run/dbus/system_bus_socket
    dbus-daemon --system --fork --nopidfile --nosyslog ||
        die "could not start the system D-Bus daemon"
fi

dbus-send --system --print-reply \
    --dest=org.freedesktop.DBus / org.freedesktop.DBus.ListNames \
    >/dev/null 2>&1 || die "the system D-Bus is not responding"

exec dbus-run-session -- mate-session "$@"
EOF_LAUNCHER
chmod 0755 /usr/local/bin/udroid-mate-x11

cat > /root/start-mate.sh <<'EOF_START'
#!/usr/bin/env bash
exec /usr/local/bin/udroid-mate-x11 "$@"
EOF_START
chmod 0755 /root/start-mate.sh
