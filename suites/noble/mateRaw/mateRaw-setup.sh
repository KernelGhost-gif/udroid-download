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
    ubuntu-mate-desktop \
    mate-utils \
    mesa-utils \
    procps \
    ubuntu-mate-default-settings \
    ubuntu-mate-icon-themes \
    ubuntu-mate-themes \
    ubuntu-mate-wallpapers-common \
    x11-utils \
    x11-xserver-utils \
    xauth \
    xdg-user-dirs \
    xterm \
    tigervnc-xorg-extension

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
required-components-list=['windowmanager', 'panel', 'filemanager']

[org/mate/desktop/session/required-components]
windowmanager='marco'
panel='mate-panel'
filemanager='caja'

[org/mate/panel]
default-layout='familiar'

# Root sessions do not reliably launch Caja as the desktop manager. The
# launcher starts Caja explicitly; these values provide its desktop backdrop.
[org/mate/desktop/background]
picture-filename='/usr/share/backgrounds/ubuntu-mate-common/Green-Wall-Logo.png'
picture-options='zoom'
EOF_SESSION_DCONF

if command -v dconf >/dev/null 2>&1; then
    dconf update
fi
