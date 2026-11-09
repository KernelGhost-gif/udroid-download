#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
	echo "ERROR: invoke this script with sudo"
	exit 1
fi

KVER="$(uname -r)"
KERNEL="/boot/vmlinuz-$KVER"
MODULES="/lib/modules/$KVER"
WORK="/tmp/nvmet-qemu-host-matrix"
ROOT="$WORK/root"
INITRD="$WORK/initramfs.cpio.gz"
PROBE="$WORK/nvmet-tail-probe"

for required in "$KERNEL" "$MODULES"; do
	[ -e "$required" ] || {
		echo "QEMU_UNSUPPORTED: missing $required"
		exit 77
	}
done

BUSYBOX="$(command -v busybox || true)"
QEMU="$(command -v qemu-system-x86_64 || true)"
KMOD="$(readlink -f "$(command -v modprobe)")"
[ -n "$BUSYBOX" ] && [ -n "$QEMU" ] && [ -x "$KMOD" ] || {
	echo "QEMU_UNSUPPORTED: busybox, QEMU, or kmod unavailable"
	exit 77
}

rm -rf "$WORK"
mkdir -p "$ROOT"/bin "$ROOT"/sbin "$ROOT"/usr/bin "$ROOT"/etc \
	"$ROOT"/proc "$ROOT"/sys "$ROOT"/dev "$ROOT"/tmp "$ROOT"/run \
	"$ROOT"/lib/modules
cp "$BUSYBOX" "$ROOT/bin/busybox"
mknod -m 600 "$ROOT/dev/console" c 5 1
mknod -m 666 "$ROOT/dev/null" c 1 3
for applet in sh mount mkdir rmdir rm ln echo cat sleep poweroff dmesg grep ifconfig basename; do
	ln -s /bin/busybox "$ROOT/bin/$applet"
done
GUEST_MODULE_DIR="$ROOT/lib/modules/$KVER"
mkdir -p "$GUEST_MODULE_DIR"
for metadata in modules.builtin modules.builtin.modinfo modules.order modules.softdep; do
	[ ! -f "$MODULES/$metadata" ] || cp "$MODULES/$metadata" "$GUEST_MODULE_DIR/"
done

MODULE_LIST="$WORK/modules-to-copy.list"
: > "$MODULE_LIST"
for name in brd nvmet-tcp nvme-tcp; do
	modprobe --set-version "$KVER" --show-depends "$name" |
		awk '$1 == "insmod" { print $2 }' >> "$MODULE_LIST"
done
sort -u -o "$MODULE_LIST" "$MODULE_LIST"

while IFS= read -r module; do
	[ -f "$module" ] || continue
	relative="${module#"$MODULES"/}"
	destination="$GUEST_MODULE_DIR/$relative"
	mkdir -p "$(dirname "$destination")"
	cp "$module" "$destination"
	case "$destination" in
		*.ko.zst) zstd -d -q "$destination"; rm -f "$destination" ;;
		*.ko.xz) xz -d -q "$destination" ;;
	esac
done < "$MODULE_LIST"

echo "QEMU_GUEST_MODULE_COUNT=$(find "$GUEST_MODULE_DIR" -type f -name '*.ko' | wc -l)"
du -sh "$ROOT/lib/modules"
depmod -b "$ROOT" "$KVER"
cp "$KMOD" "$ROOT/usr/bin/kmod"
for applet in modprobe insmod rmmod lsmod depmod; do
	ln -s /usr/bin/kmod "$ROOT/sbin/$applet"
done

while IFS= read -r lib; do
	[ -f "$lib" ] || continue
	mkdir -p "$ROOT$(dirname "$lib")"
	cp -L "$lib" "$ROOT$lib"
done < <(ldd "$KMOD" | awk '{ for (i = 1; i <= NF; i++) if ($i ~ /^\//) print $i }' | sort -u)

gcc -static -std=c11 -O2 -Wall -Wextra -Werror \
	"$(dirname "$0")/probe.c" -o "$PROBE"
cp "$PROBE" "$ROOT/bin/nvmet-tail-probe"

cat > "$ROOT/init" <<'INIT'
#!/bin/sh
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
exec >/dev/console 2>&1
set -eux
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t configfs configfs /sys/kernel/config
echo "GUEST_CMDLINE=$(cat /proc/cmdline)"
if dmesg | grep -q "heap alloc:off, heap free:off"; then
	echo "GUEST_ALLOC_INIT=OFF"
elif dmesg | grep -q "heap alloc:on, heap free:on"; then
	echo "GUEST_ALLOC_INIT=ON"
else
	echo "GUEST_ALLOC_INIT=UNKNOWN"
fi
modprobe brd rd_nr=1 rd_size=65536
modprobe nvmet-tcp
modprobe nvme-tcp
ifconfig lo up
NQN="nqn.2026-11.io.udroid:qemu-host-matrix"
HOSTNQN="nqn.2026-11.io.udroid:qemu-host"
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
while [ "$i" -lt 30 ]; do
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
	echo "GUEST_ERROR=No NVMe controller device"
	echo "GUEST_LAB_DONE"
	poweroff -f
fi
echo "GUEST_CONTROLLER=$CTRL"
/bin/nvmet-tail-probe "$CTRL" 4 64 68 4096 8188 8192 8196 16384 65536
echo "GUEST_LAB_DONE"
poweroff -f
INIT
chmod 0755 "$ROOT/init"
(
	cd "$ROOT"
	find . -print0 | cpio --null -o --format=newc 2>/dev/null | gzip -1
) > "$INITRD"

run_guest() {
	local label="$1"
	local init_alloc="$2"
	local init_free="$3"
	local log="$WORK/$label.log"
	echo "===== QEMU variant: $label (init_on_alloc=$init_alloc init_on_free=$init_free) ====="
	set +e
	timeout 240 "$QEMU" \
		-machine pc,accel=tcg \
		-cpu max \
		-m 2048 \
		-smp 2 \
		-kernel "$KERNEL" \
		-initrd "$INITRD" \
		-append "console=ttyS0,115200 rdinit=/init init_on_alloc=$init_alloc init_on_free=$init_free loglevel=7 panic=-1" \
		-nographic -monitor none -no-reboot > "$log" 2>&1
	local status=$?
	set -e
	cat "$log"
	if ! grep -q "GUEST_LAB_DONE" "$log"; then
		echo "QEMU_VARIANT=$label INCOMPLETE qemu_status=$status"
		return 1
	fi
	if grep -q "GUEST_ERROR=" "$log" || ! grep -q "^GUEST_CONTROLLER=" "$log" || ! grep -q "RESULT len=8192" "$log"; then
		echo "QEMU_VARIANT=$label FAILED_TO_RUN_NVME_PROBE"
		return 1
	fi
	if [ "$label" = "uninitialized" ] && grep -q "GUEST_ALLOC_INIT=OFF" "$log"; then
		echo "QEMU_VARIANT=$label ALLOC_INIT_CONFIRMED_OFF"
	elif [ "$label" = "hardened" ] && grep -q "GUEST_ALLOC_INIT=ON" "$log"; then
		echo "QEMU_VARIANT=$label ALLOC_INIT_CONFIRMED_ON"
	else
		echo "QEMU_VARIANT=$label ALLOC_INIT_STATE_MISMATCH"
		return 1
	fi
	grep "^RESULT " "$log" || true
}

run_guest uninitialized 0 0
run_guest hardened 1 1
echo "QEMU_MATRIX=COMPLETED"
