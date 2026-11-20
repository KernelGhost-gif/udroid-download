[![CodeFactor](https://www.codefactor.io/repository/github/thatmg393/udroid-download/badge/main)](https://www.codefactor.io/repository/github/thatmg393/udroid-download/overview/main)

# udroid-download

An ( fs-cook & debootstrap ) wrapper to build udroid with cmdline.

## Ubuntu Noble MATE X11 image

The `noble/mateRaw` target builds a lightweight Ubuntu 24.04 MATE desktop
rootfs for ARM64 and Termux:X11. It uses the MATE core desktop packages and
Ubuntu MATE defaults without installing LightDM or an in-container Xorg
server.

Build it with:

```sh
sudo bash build.sh -s noble -v mateRaw -a arm64
```

The archive is written to
`fs-cook/out/noble-mateRaw-arm64.tar.gz`. Start Termux:X11, enter the
installed Noble rootfs, and launch the desktop with:

```sh
udroid-mate-x11
```

The launcher verifies the X11 connection, starts the system D-Bus only when
needed, creates a private runtime directory for PRoot, and starts MATE with a
fresh session D-Bus. The unavailable systemd-logind activation file is kept
under `/root/login1-disabled/` to avoid trying to start logind inside PRoot.
The CI matrix builds this target for ARM64 only.

## Ubuntu Plucky GNOME X11 image

The `plucky/gnomeRaw` target builds an ARM64 Ubuntu Plucky GNOME desktop
image for Termux:X11. GNOME is launched with Mesa's `llvmpipe` software
renderer and GTK's Cairo renderer by default. The launcher clears conflicting
VirGL/KGSL/Turnip loader overrides before starting GNOME.

Build it with:

```sh
sudo bash build.sh -s plucky -v gnomeRaw -a arm64
```

The image is written to
`fs-cook/out/plucky-gnomeRaw-arm64.tar.gz`. After extracting the rootfs and
starting Termux:X11, launch GNOME from the rootfs with:

```sh
udroid-gnome-x11
```

The `udroid-gnome-x11` launcher starts the system D-Bus when needed, creates a
per-user runtime directory, checks the X11 connection, and starts a fresh
session D-Bus for GNOME. It intentionally uses CPU rendering; it does not
attempt to enable VirGL or direct KGSL acceleration.

## Resolute Plasma image

The `resolute/plasma` target builds the ARM64 Ubuntu 26.04 desktop image. It
contains Plasma X11 with the pinned MacTahoe profile, uDroid session helpers,
Firefox, Chromium, Visual Studio Code, and the daily desktop applications. The
image intentionally does not carry the older Xfce desktop.

```sh
sudo bash build.sh -s resolute -v plasma -a arm64
```

The finished archive is written to
`fs-cook/out/resolute-plasma-arm64.tar.gz`. The target is included in the CI
matrix and declares its supported architecture in `.architectures`.

## Debian Trixie Plasma image

The `trixie/plasma` target builds a Debian 13 ARM64 image with the same Plasma
X11 desktop, MacTahoe profile, session helpers, and development applications.
Chromium and Firefox ESR come directly from Debian's APT repositories, including
security updates. VS Code uses the existing verified Microsoft `.deb` installer.
Snap and Flatpak are not required.

```sh
sudo bash build.sh -s trixie -v plasma -a arm64
```

The archive is `fs-cook/out/trixie-plasma-arm64.tar.gz`. CI discovers the target
automatically; the release job adds its URL and checksum to `distro-data.json`
after a successful build and release. Both Plasma targets share the provisioning
scripts and overlay in `shared/plasma/`.

The release generator reads `ID` from each completed archive's `os-release`
file and records it in `distro-data.json`. New variants need no separate distro
mapping; conflicting IDs within one suite fail release metadata generation.
