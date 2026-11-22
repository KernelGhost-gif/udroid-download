#!/usr/bin/env bash
# Build the Ubuntu Noble MATE desktop variant for uDroid.
# shellcheck disable=SC1091

set -Ee -o pipefail
source plugins/envsetup

export OVERRIDER_COMPRESSION_TYPE SUITE ENABLE_EXIT ENABLE_USER_SETUP INCLUDE_PACKAGES
SUITE="noble"
VARIENT="mateRaw"
frn="out/${SUITE}-${VARIENT}"
OVERRIDER_COMPRESSION_TYPE="gzip"
ENABLE_EXIT=true
ENABLE_USER_SETUP=false
INCLUDE_PACKAGES="gpgv"

additional_setup() {
    cat > "$chroot_dir/etc/apt/sources.list" <<EOF_SOURCES
# Ubuntu Noble repositories for uDroid.
deb $MIRROR $SUITE main restricted universe multiverse
deb $MIRROR $SUITE-updates main restricted universe multiverse
deb $MIRROR $SUITE-backports main restricted universe multiverse
EOF_SOURCES

    # Install MATE in the image, then configure it for an external Termux:X11
    # server. No display manager or in-container Xorg server is installed.
    run_shell_script "${VARIENT}-setup.sh"
    run_shell_script "${VARIENT}-fixes.sh"
    run_shell_script "${VARIENT}-cleanup.sh"
}

case "${1:-arm64}" in
    arm64|amd64) _arch="$1" ;;
    all) _arch="arm64" ;;
    *)
        die "Noble MATE supports ARM64 and AMD64 (usage: $0 [arm64|amd64|all])"
        ;;
esac

build_target="${frn}-${_arch}"
build_complete=false
cleanup_build() {
    if [ "$build_complete" = false ]; then
        do_unmount "$build_target" || true
    fi
}
trap cleanup_build EXIT

shout "Bootstrapping Ubuntu Noble MATE [arm64]..."
do_build "$build_target" "$_arch"

shout "Packing Noble MATE root filesystem..."
do_compress "$build_target"

shout "Unmounting Noble MATE root filesystem..."
do_unmount "$build_target"
build_complete=true

shout "Noble MATE build complete. Output:"
ls -lh "${frn}-${_arch}.tar.gz"
