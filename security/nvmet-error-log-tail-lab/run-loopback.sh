#!/usr/bin/env bash
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "$0")" && pwd)"
NQN="nqn.2026-11.io.udroid:nvmet-error-log-tail-lab"
HOSTNQN="nqn.2026-11.io.udroid:github-actions-host"
PORT_ID="41"
PORT_DIR="/sys/kernel/config/nvmet/ports/$PORT_ID"
SUBSYS_DIR="/sys/kernel/config/nvmet/subsystems/$NQN"
IMAGE="/tmp/nvmet-tail-lab.img"
RAW_PROBE="/tmp/nvmet-raw-probe-$.py"
LOOP=""
CONTROLLER=""
MOUNTED_CONFIGFS_HERE=0
CONNECTED=0

cleanup() {
	set +e
	if [ "$CONNECTED" -eq 1 ] && command -v nvme >/dev/null 2>&1; then
		nvme disconnect --nqn="$NQN" >/dev/null 2>&1
	fi
	if [ -d "$SUBSYS_DIR/namespaces/1" ]; then
		echo 0 > "$SUBSYS_DIR/namespaces/1/enable" 2>/dev/null
	fi
	if [ -L "$PORT_DIR/subsystems/$NQN" ]; then
		rm -f "$PORT_DIR/subsystems/$NQN"
	fi
	rmdir "$PORT_DIR" 2>/dev/null
	rmdir "$SUBSYS_DIR/namespaces/1" 2>/dev/null
	rmdir "$SUBSYS_DIR" 2>/dev/null
	if [ -n "$LOOP" ]; then
		losetup -d "$LOOP" 2>/dev/null
	fi
	rm -f "$IMAGE" /tmp/nvmet-tail-probe "$RAW_PROBE"
	if [ "$MOUNTED_CONFIGFS_HERE" -eq 1 ]; then
		umount /sys/kernel/config 2>/dev/null
	fi
}
trap cleanup EXIT

if [ "$(id -u)" -ne 0 ]; then
	echo "ERROR: run this lab as root (the workflow invokes it through sudo)."
	exit 1
fi

echo "=== NVMe/TCP Error Log tail loopback test ==="
echo "Runner kernel: $(uname -r)"
echo "Kernel command line: $(cat /proc/cmdline)"
CONFIG_FILE="/boot/config-$(uname -r)"
if [ -r "$CONFIG_FILE" ]; then
	grep -E '^CONFIG_INIT_ON_(ALLOC|FREE)_DEFAULT_ON=' "$CONFIG_FILE" || true
elif [ -r /proc/config.gz ] && command -v zgrep >/dev/null 2>&1; then
	zgrep -E '^CONFIG_INIT_ON_(ALLOC|FREE)_DEFAULT_ON=' /proc/config.gz || true
else
	echo "Kernel build config unavailable; init-on-alloc/free defaults unknown."
fi
for parameter in /sys/module/page_alloc/parameters/init_on_alloc /sys/module/page_alloc/parameters/init_on_free; do
	if [ -r "$parameter" ]; then
		echo "$parameter=$(cat "$parameter")"
	fi
done
echo "Target NQN: $NQN"
echo "Target host policy: allow_any_host=1 (isolated loopback only)"
echo "Transport endpoint: 127.0.0.1:4420"

for module in nvmet nvmet-tcp nvme-tcp loop; do
	if ! modprobe "$module"; then
		echo "RUNTIME_UNSUPPORTED: unable to load kernel module $module"
		exit 77
	fi
done

mkdir -p /sys/kernel/config
if ! mountpoint -q /sys/kernel/config; then
	if ! mount -t configfs configfs /sys/kernel/config; then
		echo "RUNTIME_UNSUPPORTED: configfs could not be mounted"
		exit 77
	fi
	MOUNTED_CONFIGFS_HERE=1
fi

if ! command -v nvme >/dev/null 2>&1; then
	echo "ERROR: nvme-cli is not installed"
	exit 1
fi

truncate -s 64M "$IMAGE"
if ! LOOP="$(losetup --find --show "$IMAGE")"; then
	echo "RUNTIME_UNSUPPORTED: loop devices are unavailable"
	exit 77
fi

mkdir "$SUBSYS_DIR"
echo 1 > "$SUBSYS_DIR/attr_allow_any_host"
mkdir "$SUBSYS_DIR/namespaces/1"
echo "$LOOP" > "$SUBSYS_DIR/namespaces/1/device_path"
echo 1 > "$SUBSYS_DIR/namespaces/1/enable"

mkdir "$PORT_DIR"
echo 127.0.0.1 > "$PORT_DIR/addr_traddr"
echo tcp > "$PORT_DIR/addr_trtype"
echo ipv4 > "$PORT_DIR/addr_adrfam"
echo 4420 > "$PORT_DIR/addr_trsvcid"
ln -s "$SUBSYS_DIR" "$PORT_DIR/subsystems/$NQN"

nvme connect \
	--transport=tcp \
	--traddr=127.0.0.1 \
	--trsvcid=4420 \
	--nqn="$NQN" \
	--hostnqn="$HOSTNQN"
CONNECTED=1

for attempt in $(seq 1 30); do
	for sysdev in /sys/class/nvme/nvme[0-9]*; do
		[ -r "$sysdev/subsysnqn" ] || continue
		if [ "$(cat "$sysdev/subsysnqn")" = "$NQN" ]; then
			CONTROLLER="/dev/$(basename "$sysdev")"
			break 2
		fi
	done
	sleep 1
done

if [ -z "$CONTROLLER" ] || [ ! -c "$CONTROLLER" ]; then
	echo "ERROR: connected controller device was not created"
	exit 1
fi

echo "Connected controller: $CONTROLLER"
cc -std=c11 -O2 -Wall -Wextra -Werror \
	"$HERE/probe.c" -o /tmp/nvmet-tail-probe

echo "=== Running 8 KiB baseline, then oversized reads ==="
/tmp/nvmet-tail-probe "$CONTROLLER" 4 64 68 4096 8188 8192 8196 16384 65536

echo "=== Raw NVMe/TCP probe as unprivileged user ==="
if ! command -v runuser >/dev/null 2>&1; then
	echo "RAW_TCP_PROBE=SKIPPED (runuser unavailable)"
	exit 77
fi
cp "$HERE/raw_tcp_probe.py" "$RAW_PROBE"
chmod 0644 "$RAW_PROBE"
set +e
runuser -u nobody -- python3 "$RAW_PROBE" \
	--host 127.0.0.1 --port 4420 --subsysnqn "$NQN"
RAW_STATUS=$?
set -e
if [ "$RAW_STATUS" -ne 0 ]; then
	echo "=== target kernel diagnostics after raw protocol probe ==="
	dmesg | tail -n 100
	exit "$RAW_STATUS"
fi
sleep 1

echo "=== Runtime test completed ==="
echo "Raw response bytes were not written to disk or uploaded."
