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
export XKB_CONFIG_ROOT=/usr/share/X11/xkb
export GSK_RENDERER="${GSK_RENDERER:-cairo}"
# MATE's mate-wm helper uses this variable to choose the actual window manager.
export WINDOW_MANAGER=/usr/bin/marco

# Android PRoot has no logind-managed /run/user/<uid>. Use a private,
# writable runtime directory instead.
export XDG_RUNTIME_DIR="${UDROID_MATE_RUNTIME_DIR:-/tmp/mate-runtime-$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR" || die "cannot create $XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR" || die "cannot secure $XDG_RUNTIME_DIR"

command -v mate-session >/dev/null 2>&1 || die "mate-session is not installed"
command -v dbus-send >/dev/null 2>&1 || die "dbus-send is not installed"
command -v dbus-daemon >/dev/null 2>&1 || die "dbus-daemon is not installed"
command -v dbus-run-session >/dev/null 2>&1 || die "dbus-run-session is not installed"
command -v gsettings >/dev/null 2>&1 || die "gsettings is not installed"

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

# The full Ubuntu MATE Mutiny desktop uses a top panel and a left Plank dock.
# Write these values to the writable user dconf database before session start;
# this avoids relying only on system defaults under a root-owned PRoot session.
exec dbus-run-session -- bash -c '
set -Eeuo pipefail

command -v plank >/dev/null 2>&1 || {
    printf "[udroid-mate] ERROR: Plank is not installed\n" >&2
    exit 1
}
command -v dconf >/dev/null 2>&1 || {
    printf "[udroid-mate] ERROR: dconf is not installed\n" >&2
    exit 1
}

gsettings set org.mate.session required-components-list "[\"windowmanager\", \"panel\", \"filemanager\", \"dock\"]"
gsettings set org.mate.session.required-components dock "plank"
gsettings set org.mate.panel default-layout "mutiny"
gsettings set org.mate.interface gtk-theme "Yaru-MATE-dark"
gsettings set org.mate.interface icon-theme "Yaru-MATE-dark"
gsettings set org.mate.Marco.general theme "Yaru-MATE-dark"
gsettings set org.mate.Marco.general compositing-manager true
gsettings set org.mate.caja.desktop computer-icon-visible false
gsettings set org.mate.caja.desktop home-icon-visible false
gsettings set org.mate.caja.desktop trash-icon-visible false
gsettings set org.mate.caja.desktop volumes-visible false
gsettings set org.mate.background picture-filename "/usr/share/backgrounds/ubuntu-mate-hirsute/Hirsute-Hippo_WP_2560x1440_Grey.png"
gsettings set org.mate.background picture-options "zoom"

# These are the same values mate-tweak applies when selecting the Mutiny dock.
dconf write /net/launchpad/plank/docks/dock1/alignment "\"fill\""
dconf write /net/launchpad/plank/docks/dock1/hide-mode "\"none\""
dconf write /net/launchpad/plank/docks/dock1/icon-size 56
dconf write /net/launchpad/plank/docks/dock1/items-alignment "\"start\""
dconf write /net/launchpad/plank/docks/dock1/position "\"left\""
dconf write /net/launchpad/plank/docks/dock1/theme "\"Mutiny-dark\""
dconf write /net/launchpad/plank/docks/dock1/zoom-enabled false

panel_layout="$(gsettings get org.mate.panel default-layout)"
dock_component="$(gsettings get org.mate.session.required-components dock)"
if [[ "$panel_layout" != *mutiny* || "$dock_component" != *plank* ]]; then
    printf "[udroid-mate] ERROR: expected Mutiny with Plank, got layout=%s dock=%s\n" "$panel_layout" "$dock_component" >&2
    exit 1
fi
printf "[udroid-mate] Ubuntu MATE panel layout: %s\n" "$panel_layout"
printf "[udroid-mate] Required dock component: %s\n" "$dock_component"

mate-session "$@" &
session_pid=$!
desktop_pid=""
fallback_dock_pid=""

cleanup_session() {
    if [[ -n "$desktop_pid" ]]; then
        kill "$desktop_pid" 2>/dev/null || true
    fi
    if [[ -n "$fallback_dock_pid" ]]; then
        kill "$fallback_dock_pid" 2>/dev/null || true
    fi
    kill "$session_pid" 2>/dev/null || true
    wait "$session_pid" 2>/dev/null || true
}
trap cleanup_session EXIT INT TERM

components_ready=false
for attempt in $(seq 1 90); do
    if ! kill -0 "$session_pid" 2>/dev/null; then
        wait "$session_pid"
        exit $?
    fi
    if pgrep -x marco >/dev/null 2>&1 &&
       pgrep -x mate-panel >/dev/null 2>&1 &&
       pgrep -x plank >/dev/null 2>&1; then
        components_ready=true
        break
    fi
    sleep 1
done

if [[ "$components_ready" != true ]]; then
    # Some PRoot session managers fail to resolve the dock desktop entry even
    # though the component is configured. Start Plank directly as a fallback.
    if ! pgrep -x plank >/dev/null 2>&1; then
        plank >> "$XDG_RUNTIME_DIR/plank.log" 2>&1 &
        fallback_dock_pid=$!
    fi
    for attempt in $(seq 1 15); do
        if pgrep -x marco >/dev/null 2>&1 &&
           pgrep -x mate-panel >/dev/null 2>&1 &&
           pgrep -x plank >/dev/null 2>&1; then
            components_ready=true
            break
        fi
        sleep 1
    done
fi

if [[ "$components_ready" != true ]]; then
    printf "[udroid-mate] ERROR: Marco, MATE Panel, and Plank did not become ready\n" >&2
    cat "$XDG_RUNTIME_DIR/plank.log" >&2 || true
    exit 1
fi

panel_layout="$(gsettings get org.mate.panel default-layout)"
dock_component="$(gsettings get org.mate.session.required-components dock)"
dock_position="$(dconf read /net/launchpad/plank/docks/dock1/position)"
gtk_theme="$(gsettings get org.mate.interface gtk-theme)"
icon_theme="$(gsettings get org.mate.interface icon-theme)"
background="$(gsettings get org.mate.background picture-filename)"
if [[ "$panel_layout" != *mutiny* || "$dock_component" != *plank* || "$dock_position" != *left* ]]; then
    printf "[udroid-mate] ERROR: live desktop settings are not Mutiny/Plank-left (layout=%s dock=%s position=%s)\n" "$panel_layout" "$dock_component" "$dock_position" >&2
    exit 1
fi
{
    printf "layout=%s\n" "$panel_layout"
    printf "dock_component=%s\n" "$dock_component"
    printf "dock_position=%s\n" "$dock_position"
    printf "gtk_theme=%s\n" "$gtk_theme"
    printf "icon_theme=%s\n" "$icon_theme"
    printf "background=%s\n" "$background"
    printf "plank_process=running\n"
} > "$XDG_RUNTIME_DIR/mate-desktop-state.txt"
printf "[udroid-mate] Plank dock position: %s\n" "$dock_position"

# Caja owns the desktop background even when all desktop icons are hidden.
caja --no-default-window --force-desktop \
    >> "$XDG_RUNTIME_DIR/caja-desktop.log" 2>&1 &
desktop_pid=$!

desktop_ready=false
for attempt in $(seq 1 30); do
    if pgrep -x caja >/dev/null 2>&1; then
        desktop_ready=true
        break
    fi
    sleep 1
done
if [[ "$desktop_ready" != true ]]; then
    printf "[udroid-mate] ERROR: Caja desktop process did not stay running\n" >&2
    cat "$XDG_RUNTIME_DIR/caja-desktop.log" >&2 || true
    exit 1
fi
printf "[udroid-mate] Caja desktop is running (pid %s)\n" "$desktop_pid"

# Allow Brisk Menu and Ayatana indicator factories to finish activating.
sleep 3
wait "$session_pid"

' udroid-mate-session "$@"
EOF_LAUNCHER
chmod 0755 /usr/local/bin/udroid-mate-x11

cat > /root/start-mate.sh <<'EOF_START'
#!/usr/bin/env bash
exec /usr/local/bin/udroid-mate-x11 "$@"
EOF_START
chmod 0755 /root/start-mate.sh
