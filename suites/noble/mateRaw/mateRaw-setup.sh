#!/usr/bin/env bash
# Install the MATE desktop without a display manager or a bundled X server.
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
    mate-desktop-environment-core \
    mate-utils \
    mesa-utils \
    ubuntu-mate-default-settings \
    ubuntu-mate-icon-themes \
    ubuntu-mate-wallpapers-common \
    x11-utils \
    x11-xserver-utils \
    xauth \
    xdg-user-dirs \
    xterm

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

install -d /etc/dconf/profile
cat > /etc/dconf/profile/user <<'EOF_DCONF'
user-db:user
system-db:local
EOF_DCONF

if command -v dconf >/dev/null 2>&1; then
    dconf update
fi
