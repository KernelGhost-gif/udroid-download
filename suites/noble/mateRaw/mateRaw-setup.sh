#!/usr/bin/env bash
# Install the full Ubuntu MATE desktop package set used by Jammy. Xorg is a
# dependency of ubuntu-mate-desktop, but it is never started; Termux:X11 is the
# active display server and PRoot does not use LightDM.
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true
export LC_ALL=C
export LANGUAGE=C
export LANG=C

apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates \
    dbus \
    dbus-bin \
    dbus-x11 \
    dconf-cli \
    fonts-noto-color-emoji \
    fonts-ubuntu \
    gvfs \
    gvfs-backends \
    libgl1-mesa-dri \
    locales \
    mate-applets \
    mate-indicator-applet \
    mate-applet-brisk-menu \
    mate-tweak \
    mate-control-center \
    mate-terminal \
    plank \
    ayatana-indicator-application \
    ayatana-indicator-datetime \
    ayatana-indicator-messages \
    ayatana-indicator-notifications \
    ayatana-indicator-power \
    ayatana-indicator-printers \
    ayatana-indicator-session \
    ayatana-indicator-sound \
    atril \
    engrampa \
    eom \
    epiphany-browser \
    ffmpegthumbnailer \
    gnome-keyring \
    mate-calc \
    mate-media \
    mate-notification-daemon \
    mate-system-monitor \
    pluma \
    ubuntu-mate-desktop \
    mate-utils \
    mesa-utils \
    procps \
    ubuntu-mate-default-settings \
    ubuntu-mate-icon-themes \
    ubuntu-mate-themes \
    ubuntu-mate-wallpapers-common \
    ubuntu-mate-wallpapers-hirsute \
    x11-utils \
    x11-xserver-utils \
    xauth \
    xdg-user-dirs \
    xterm \
    tigervnc-xorg-extension


# Mutiny's upstream Plank profile includes a Firefox Snap launcher.
# Snap services do not run in Termux PRoot, so use a normal Ubuntu browser.
install -d /usr/share/applications
cat > /usr/share/applications/udroid-web-browser.desktop <<'EOF_BROWSER'
[Desktop Entry]
Version=1.0
Type=Application
Name=Web Browser
Comment=Browse the web
Exec=epiphany %U
Icon=web-browser
Terminal=false
Categories=Network;WebBrowser;
StartupNotify=true
EOF_BROWSER

# Seed the Mutiny dock explicitly. Ubuntu's settings-overlay is normally
# applied during an interactive first login; a root-owned PRoot image has no
# such first-login flow, so relying on that optional overlay leaves Plank empty.
install -d /root/.config/plank/dock1/launchers

cat > /usr/share/applications/caja-browser.desktop <<'EOF_CAJA'
[Desktop Entry]
Version=1.0
Type=Application
Name=Files
Comment=Browse files
Exec=caja --no-desktop
Icon=system-file-manager
Terminal=false
Categories=System;FileTools;FileManager;
StartupNotify=true
EOF_CAJA

cat > /usr/share/applications/matecc.desktop <<'EOF_CONTROL'
[Desktop Entry]
Version=1.0
Type=Application
Name=Control Center
Comment=Configure the MATE desktop
Exec=mate-control-center
Icon=preferences-system
Terminal=false
Categories=Settings;
StartupNotify=true
EOF_CONTROL

cat > /usr/share/applications/udroid-terminal.desktop <<'EOF_TERMINAL'
[Desktop Entry]
Version=1.0
Type=Application
Name=Terminal
Comment=Use the command line
Exec=mate-terminal
Icon=utilities-terminal
Terminal=false
Categories=System;TerminalEmulator;
StartupNotify=true
EOF_TERMINAL

cat > /root/.config/plank/dock1/launchers/caja-browser.dockitem <<'EOF_DOCK_CAJA'
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/caja-browser.desktop
EOF_DOCK_CAJA
cat > /root/.config/plank/dock1/launchers/clock.dockitem <<'EOF_DOCK_CLOCK'
[PlankDockItemPreferences]
Launcher=docklet://clock

[DockyClockPreferences]
ShowMilitary=false
ShowDigital=false
ShowDate=false
EOF_DOCK_CLOCK
cat > /root/.config/plank/dock1/launchers/desktop.dockitem <<'EOF_DOCK_DESKTOP'
[PlankDockItemPreferences]
Launcher=docklet://desktop
EOF_DOCK_DESKTOP
cat > /root/.config/plank/dock1/launchers/firefox_firefox.dockitem <<'EOF_DOCK_BROWSER'
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/udroid-web-browser.desktop
EOF_DOCK_BROWSER
cat > /root/.config/plank/dock1/launchers/mate-calc.dockitem <<'EOF_DOCK_CALC'
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/mate-calc.desktop
EOF_DOCK_CALC
cat > /root/.config/plank/dock1/launchers/mate-system-monitor.dockitem <<'EOF_DOCK_MONITOR'
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/mate-system-monitor.desktop
EOF_DOCK_MONITOR
cat > /root/.config/plank/dock1/launchers/mate-terminal.dockitem <<'EOF_DOCK_TERM'
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/udroid-terminal.desktop
EOF_DOCK_TERM
cat > /root/.config/plank/dock1/launchers/matecc.dockitem <<'EOF_DOCK_CONTROL'
[PlankDockItemPreferences]
Launcher=file:///usr/share/applications/matecc.desktop
EOF_DOCK_CONTROL
cat > /root/.config/plank/dock1/launchers/trash.dockitem <<'EOF_DOCK_TRASH'
[PlankDockItemPreferences]
Launcher=docklet://trash
EOF_DOCK_TRASH


# Use a predictable locale in graphical applications.
if [ -f /etc/locale.gen ]; then
    sed -i 's/^# *en_US.UTF-8 UTF-8$/en_US.UTF-8 UTF-8/' /etc/locale.gen
fi
locale-gen en_US.UTF-8
update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Seed conventional user directories and MATE defaults for the rootfs user.
# xdg-user-dirs writes its configuration below ~/.config; create that path
# explicitly because the image has no interactive first-login setup.
install -d /root/.config /root/Desktop /root/Downloads /root/Documents \
    /root/Pictures /root/Music /root/Videos
if command -v xdg-user-dirs-update >/dev/null 2>&1; then
    if ! HOME=/root XDG_CONFIG_HOME=/root/.config xdg-user-dirs-update; then
        echo "[udroid-mate] WARNING: could not update user directories; continuing with the pre-created directories" >&2
    fi
fi

install -d /etc/dconf/profile /etc/dconf/db/local.d
cat > /etc/dconf/profile/user <<'EOF_DCONF'
user-db:user
system-db:local
EOF_DCONF

# dconf keyfile groups use schema paths, not schema IDs. Keep the
# required MATE components explicit so PRoot does not depend on session defaults.
cat > /etc/dconf/db/local.d/00-udroid-mate-session <<'EOF_SESSION_DCONF'
[org/mate/desktop/session]
required-components-list=['panel']

[org/mate/desktop/session/required-components]
windowmanager='marco'
panel='mate-panel'
filemanager='caja'
dock='plank'

[org/mate/panel]
default-layout='mutiny'

# Match the dark Ubuntu MATE 21.04 Mutiny appearance in the reference desktop.
[org/mate/interface]
gtk-theme='Yaru-MATE-dark'
icon-theme='Yaru-MATE-dark'

[org/mate/Marco/general]
theme='Yaru-MATE-dark'
compositing-manager=true

# Mutiny uses Brisk Menu's dash presentation and retains its visible Menu label.
[com/solus-project/brisk-menu]
window-type='dash'
label-visible=true

# Keep the desktop clear; the launchers live in the left-side Plank dock.
[org/mate/caja/desktop]
computer-icon-visible=false
home-icon-visible=false
trash-icon-visible=false
volumes-visible=false

[org/mate/desktop/background]
picture-filename='/usr/share/backgrounds/ubuntu-mate-hirsute/Hirsute-Hippo_WP_2560x1440_Grey.png'
picture-options='zoom'
EOF_SESSION_DCONF

if command -v dconf >/dev/null 2>&1; then
    dconf update
fi
