#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 2 ]; then
	echo "usage: $0 <bzImage> <output-log>"
	exit 64
fi
KERNEL="$(realpath "$1")"
LOG="$(realpath -m "$2")"
WORK="/tmp/nvmet-kmsan-guest"
ROOT="$WORK/root"
INITRD="$WORK/initramfs.cpio.gz"
QEMU="$(command -v qemu-system-x86_64 || true)"
BUSYBOX="$(command -v busybox || true)"

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
[ -n "$QEMU" ] && [ -n "$BUSYBOX" ] || {
	echo "KMSAN_QEMU_UNSUPPORTED: missing QEMU or static BusyBox"
	exit 77
}

rm -rf "$WORK"
mkdir -p "$ROOT"/bin "$ROOT"/proc "$ROOT"/sys "$ROOT"/dev \
	"$ROOT"/tmp "$ROOT"/run
cp "$BUSYBOX" "$ROOT/bin/busybox"
for applet in sh mount mkdir rmdir rm ln echo cat sleep poweroff dmesg grep ifconfig basename; do
	ln -s /bin/busybox "$ROOT/bin/$applet"
done
gcc -static -std=c11 -O2 -Wall -Wextra -Werror \
	"$(dirname "$0")/probe.c" -o "$ROOT/bin/nvmet-tail-probe"
mknod -m 600 "$ROOT/dev/console" c 5 1
mknod -m 666 "$ROOT/dev/null" c 1 3

cat > "$ROOT/init" <<'INIT'
#!/bin/sh
PATH=/bin
export PATH
exec >/dev/console 2>&1
set -ex
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t configfs configfs /sys/kernel/config

echo "KMSAN_GUEST_CMDLINE=$(cat /proc/cmdline)"
if dmesg | grep -q "KernelMemorySanitizer"; then
	echo "KMSAN_RUNTIME=INITIALIZED"
else
	echo "KMSAN_RUNTIME=NOT_CONFIRMED"
fi
if dmesg | grep -q "heap alloc:off"; then
	echo "KMSAN_ALLOC_INIT=OFF"
else
	echo "KMSAN_ALLOC_INIT=ON_OR_UNKNOWN"
fi

if ! [ -c /dev/ram0 ]; then
	echo "KMSAN_GUEST_ERROR=missing builtin brd namespace device"
	echo "KMSAN_GUEST_DONE"
	poweroff -f
fi
ifconfig lo up
NQN="nqn.2026-11.io.udroid:kmsan-tail-lab"
HOSTNQN="nqn.2026-11.io.udroid:kmsan-host"
SUBSYS="/sys/kernel/config/nvmet/subsystems/$NQN"
PORT="/sys/kernel/config/nvmet/ports/41"

mkdir "$SUBSYS"
echo 1 > "$SUBSYS/attr_allow_any_host"
mkdir "$SUBSYS/namespaces/1"
echo /dev/ram0 > "$SUBSYS/namespaces/1/device_path"
echo 1 > "$SUBSYS/namespaces/1/enable"
mkdir "$PORT"
echo 127.0.0.1 > "$PORT/addr_traddr"
echo tcp > "$PORT/addr_trtype"
echo ipv4 > "$PORT/addr_adrfam"
echo 4420 > "$PORT/addr_trsvcid"
ln -s "$SUBSYS" "$PORT/subsystems/$NQN"

echo "transport=tcp,traddr=127.0.0.1,trsvcid=4420,nqn=$NQN" > /dev/nvme-fabrics
CTRL=""
i=0
while [ "$i" -lt 60 ]; do
	for sysdev in /sys/class/nvme/nvme*; do
		[ -r "$sysdev/subsysnqn" ] || continue
		if [ "$(cat "$sysdev/subsysnqn")" = "$NQN" ]; then
			CTRL="/dev/$(basename "$sysdev")"
			break 2
		fi
	done
	i=$((i + 1))
	sleep 1
done
if [ -z "$CTRL" ] || [ ! -c "$CTRL" ]; then
	echo "KMSAN_GUEST_ERROR=No NVMe controller device"
	echo "KMSAN_GUEST_DONE"
	poweroff -f
fi

echo "KMSAN_CONTROLLER=$CTRL"
/bin/nvmet-tail-probe "$CTRL" 8192 16384 65536
echo "KMSAN_GUEST_DONE"
poweroff -f
INIT
chmod 0755 "$ROOT/init"
(
	cd "$ROOT"
	find . -print0 | cpio --null -o --format=newc 2>/dev/null | gzip -1
) > "$INITRD"

echo "Booting KMSAN kernel under QEMU TCG (8 GiB guest RAM)"
set +e
timeout 360 "$QEMU" \
	-machine pc,accel=tcg \
	-cpu max \
	-m 8192 \
	-smp 2 \
	-kernel "$KERNEL" \
	-initrd "$INITRD" \
	-append "console=ttyS0,115200 rdinit=/init init_on_alloc=0 init_on_free=0 loglevel=7 panic=-1" \
	-nographic -monitor none -no-reboot > "$LOG" 2>&1
QEMU_STATUS=$?
set -e

cat "$LOG"
if ! grep -q "KMSAN_GUEST_DONE" "$LOG"; then
	echo "KMSAN_QEMU=INCOMPLETE qemu_status=$QEMU_STATUS"
	exit 1
fi
if grep -q "KMSAN_GUEST_ERROR=" "$LOG" || \
   ! grep -q "^KMSAN_CONTROLLER=" "$LOG" || \
   ! grep -q "RESULT len=8192" "$LOG"; then
	echo "KMSAN_QEMU=GUEST_PROBE_FAILED"
	exit 1
fi
if grep -q "BUG: KMSAN:" "$LOG"; then
	echo "KMSAN_RESULT=REPORT_DETECTED"
	grep -n -A18 -B3 "BUG: KMSAN:" "$LOG" || true
else
	echo "KMSAN_RESULT=NO_REPORT_OBSERVED (not proof of absence)"
fi
echo "KMSAN_QEMU=COMPLETED"
