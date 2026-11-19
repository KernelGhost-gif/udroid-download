#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 5 ]; then
  echo "usage: $0 <target> <driver-name> <qemu-device-model> <bzImage> <log-file>"
  exit 64
fi

TARGET="$1"
DRIVER="$2"
MODEL="$3"
KERNEL="$(realpath "$4")"
LOG="$(realpath -m "$5")"
QEMU="$(command -v qemu-system-x86_64 || true)"
BUSYBOX="$(command -v busybox || true)"
WORK="/tmp/virtio-restore-kasan-$TARGET"
ROOT="$WORK/root"
INITRD="$WORK/initramfs.cpio.gz"
QMP="$WORK/qmp.sock"

[ "$(id -u)" -eq 0 ] || { echo "Run this script with sudo"; exit 1; }
[ -n "$QEMU" ] && [ -n "$BUSYBOX" ] || {
  echo "UNSUPPORTED: qemu-system-x86_64 or static BusyBox is missing"
  exit 77
}
[ -s "$KERNEL" ] || { echo "Missing kernel image: $KERNEL"; exit 1; }

if ! "$QEMU" -device help 2>&1 | grep -Fq "$MODEL"; then
  echo "UNSUPPORTED: installed QEMU has no $MODEL model"
  exit 77
fi

rm -rf "$WORK"
mkdir -p "$ROOT"/bin "$ROOT"/sbin "$ROOT"/proc "$ROOT"/sys \
  "$ROOT"/dev "$ROOT"/tmp "$ROOT"/run
cp "$BUSYBOX" "$ROOT/bin/busybox"
for applet in sh mount mkdir rm ln echo cat sleep poweroff dmesg grep readlink basename cut; do
  ln -s /bin/busybox "$ROOT/bin/$applet"
done
mknod -m 600 "$ROOT/dev/console" c 5 1
mknod -m 666 "$ROOT/dev/null" c 1 3

cat > "$ROOT/init" <<'GUEST_INIT'
#!/bin/sh
PATH=/bin:/sbin
export PATH
exec >/dev/console 2>&1
set -x

mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev

DRIVER=""
for arg in $(cat /proc/cmdline); do
  case "$arg" in
    ci_driver=*) DRIVER="$(echo "$arg" | cut -d= -f2-)" ;;
  esac
done
echo "GUEST_TARGET_DRIVER=$DRIVER"
if [ -z "$DRIVER" ]; then
  echo "GUEST_UNSUPPORTED=missing driver argument"
  echo GUEST_DONE
  poweroff -f
fi

DEVICE=""
for path in /sys/bus/virtio/devices/virtio*; do
  [ -L "$path/driver" ] || continue
  bound="$(basename "$(readlink -f "$path/driver")")"
  if [ "$bound" = "$DRIVER" ]; then
    DEVICE="$(basename "$path")"
    break
  fi
done
if [ -z "$DEVICE" ]; then
  echo "GUEST_UNSUPPORTED=driver did not bind"
  echo GUEST_DONE
  poweroff -f
fi
echo "GUEST_VIRTIO_DEVICE=$DEVICE"
echo "GUEST_BOUND_DRIVER=$DRIVER"

if [ ! -r /sys/power/state ] || ! grep -qw mem /sys/power/state; then
  echo "GUEST_UNSUPPORTED=mem sleep unavailable"
  echo GUEST_DONE
  poweroff -f
fi
if [ -r /sys/power/mem_sleep ] && grep -qw deep /sys/power/mem_sleep; then
  echo deep > /sys/power/mem_sleep
fi

echo GUEST_SLEEP_BEGIN
echo mem > /sys/power/state
echo GUEST_RESUME_RETURNED

if [ -e "/sys/bus/virtio/drivers/$DRIVER/unbind" ]; then
  echo "$DEVICE" > "/sys/bus/virtio/drivers/$DRIVER/unbind"
  echo GUEST_UNBIND_REQUESTED
else
  echo "GUEST_ERROR=driver unbind control missing"
fi

sleep 1
if dmesg | grep -q "VIRTIO_RESTORE_KASAN_TEST_FAIL"; then
  echo GUEST_RESTORE_FAILURE_INJECTED
else
  echo GUEST_RESTORE_FAILURE_NOT_OBSERVED
fi
if dmesg | grep -q "BUG: KASAN:.*use-after-free"; then
  echo KASAN_REPRODUCED
else
  echo RESTORE_FAILURE_INJECTED_NO_KASAN_REPORT
fi
dmesg | tail -n 100
echo GUEST_DONE
poweroff -f
GUEST_INIT
chmod 0755 "$ROOT/init"

(
  cd "$ROOT"
  find . -print0 | cpio --null -o --format=newc 2>/dev/null | gzip -1
) > "$INITRD"

QEMU_ARGS=(
  -machine q35,accel=tcg
  -cpu max
  -m 4096
  -smp 2
  -nodefaults
  -kernel "$KERNEL"
  -initrd "$INITRD"
  -append "console=ttyS0,115200 rdinit=/init loglevel=8 kasan_multi_shot=1 panic_on_warn=0 virtio_pci_ci_fail_restore_find_vqs=1 ci_driver=$DRIVER"
  -display none
  -serial "file:$LOG"
  -monitor none
  -qmp "unix:$QMP,server=on,wait=off"
  -no-reboot
)

case "$TARGET" in
  virtio-input)
    QEMU_ARGS+=(-device virtio-keyboard-pci)
    ;;
  virtio-net)
    QEMU_ARGS+=(-netdev user,id=net0 -device virtio-net-pci,netdev=net0)
    ;;
  virtio-snd)
    QEMU_ARGS+=(-audiodev driver=none,id=audio0 -device virtio-sound-pci,audiodev=audio0)
    ;;
  virtio-console)
    QEMU_ARGS+=(-device virtio-serial-pci)
    ;;
  virtio-gpu)
    QEMU_ARGS+=(-device virtio-gpu-pci)
    ;;
  virtio-crypto)
    QEMU_ARGS+=(-object cryptodev-backend-builtin,id=crypto0 -device virtio-crypto-pci,cryptodev=crypto0)
    ;;
  virtio-can)
    QEMU_ARGS+=(-device virtio-can-pci)
    ;;
  *)
    echo "Unknown target: $TARGET"
    exit 64
    ;;
esac

echo "Starting QEMU target=$TARGET driver=$DRIVER model=$MODEL"
"$QEMU" "$$QEMU_ARGS[@]}" > "$WORK/qemu.stderr" 2>&1 &
QEMU_PID=$!
cleanup() {
  set +e
  kill "$QEMU_PID" 2>/dev/null
  wait "$QEMU_PID" 2>/dev/null
}
trap cleanup EXIT

python3 - "$QMP" "$LOG" <<'QMP_WAKE'
import json
import os
import socket
import sys
import time

path, serial_log = sys.argv[1:3]
deadline = time.monotonic() + 180
sock = None
buffer = b""
next_id = 1

def read_line(timeout=3.0):
    global buffer
    end = time.monotonic() + timeout
    while b"\n" not in buffer:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("QMP response timeout")
        sock.settimeout(remaining)
        chunk = sock.recv(8192)
        if not chunk:
            raise ConnectionError("QMP socket closed")
        buffer += chunk
    line, buffer = buffer.split(b"\n", 1)
    return line.strip()

def read_json(timeout=3.0):
    while True:
        line = read_line(timeout)
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue

def execute(command):
    global next_id
    ident = next_id
    next_id += 1
    payload = json.dumps({"execute": command, "id": ident}).encode() + b"\r\n"
    sock.sendall(payload)
    while True:
        response = read_json()
        if response.get("id") == ident:
            if "error" in response:
                raise RuntimeError(response["error"])
            return response.get("return", {})

while time.monotonic() < deadline and not os.path.exists(path):
    time.sleep(0.2)
if not os.path.exists(path):
    print("QMP_ERROR=socket_not_created")
    sys.exit(1)

for attempt in range(30):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(path)
        break
    except OSError:
        if sock:
            sock.close()
        sock = None
        time.sleep(0.2)
else:
    print("QMP_ERROR=connect_failed")
    sys.exit(1)

try:
    greeting = read_json()
    if "QMP" not in greeting:
        raise RuntimeError("QMP greeting missing")
    execute("qmp_capabilities")
    saw_sleep = False
    wake_count = 0
    while time.monotonic() < deadline:
        try:
            with open(serial_log, "r", errors="replace") as f:
                log = f.read()
        except FileNotFoundError:
            log = ""
        if "GUEST_DONE" in log:
            break
        if "GUEST_SLEEP_BEGIN" in log:
            saw_sleep = True
            status = execute("query-status").get("status", "unknown")
            print("QMP_VM_STATUS=" + str(status))
            if status in ("suspended", "paused") or wake_count == 0:
                execute("system_wakeup")
                wake_count += 1
                print("QMP_SYSTEM_WAKEUP_SENT")
            elif wake_count < 4:
                time.sleep(2)
                execute("system_wakeup")
                wake_count += 1
                print("QMP_SYSTEM_WAKEUP_RETRY")
        time.sleep(0.5)
    if not saw_sleep:
        print("QMP_ERROR=guest_never_requested_sleep")
        sys.exit(1)
finally:
    sock.close()
QMP_WAKE

for _ in $(seq 1 240); do
  if grep -q "GUEST_DONE" "$LOG" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$QEMU_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

if kill -0 "$QEMU_PID" 2>/dev/null; then
  echo "QEMU_TIMEOUT=guest_did_not_finish"
  exit 1
fi
wait "$QEMU_PID" || true
trap - EXIT
cat "$WORK/qemu.stderr" || true
cat "$LOG"

if grep -q "GUEST_UNSUPPORTED=" "$LOG"; then
  echo "UNSUPPORTED: guest setup could not bind or suspend target device"
  exit 77
fi
if ! grep -q "GUEST_DONE" "$LOG"; then
  echo "INCONCLUSIVE: guest did not complete"
  exit 1
fi
if grep -q "KASAN_REPRODUCED" "$LOG"; then
  echo "KASAN_REPRODUCED target=$TARGET"
elif grep -q "GUEST_RESTORE_FAILURE_INJECTED" "$LOG"; then
  echo "RESTORE_FAILURE_INJECTED_NO_KASAN_REPORT target=$TARGET"
else
  echo "RESTORE_FAILURE_NOT_OBSERVED target=$TARGET"
  exit 1
fi
