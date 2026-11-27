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

# Seed the same Plank launchers supplied by Ubuntu MATE. Replace only the
# Snap browser entry; the rest of the Mutiny dock remains the upstream set.
plank_launchers=/usr/share/ubuntu-mate/settings-overlay/config/plank/dock1/launchers
if [ -d "$plank_launchers" ]; then
    install -d /root/.config/plank/dock1/launchers
    cp -a "$plank_launchers/." /root/.config/plank/dock1/launchers/
    firefox_launcher=/root/.config/plank/dock1/launchers/firefox_firefox.dockitem
    if [ -f "$firefox_launcher" ]; then
        sed -i \
            's#file:///var/lib/snapd/desktop/applications/firefox_firefox.desktop#file:///usr/share/applications/udroid-web-browser.desktop#g' \
            "$firefox_launcher"
    fi
fi

# Use a predictable locale in graphical applications.
if [ -f /etc/locale.gen ]; then
    sed -i 's/^# *en_US.UTF-8 UTF-8$/en_US.UTF-8 UTF-8/' /etc/locale.gen
fi
locale-gen en_US.UTF-8
update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Seed conventional user directories and MATE defaults for the rootfs user.
install -d /root/Desktop /root/Downloads /root/Documents /root/Pictures \
    /root/Music /root/Videos
if command -v xdg-user-dirs-update >/dev/null 2>&1; then
    HOME=/root xdg-user-dirs-update
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
required-components-list=['windowmanager', 'panel', 'filemanager', 'dock']

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
