# Virtio failed-restore KASAN lab

This is an isolated CI experiment for the source-level stale-virtqueue lifetime paths audited in upstream Linux commit `20eef29b7c8465ac98d7dd6d6f2ac07a8d8056ca`.

## What the test does

1. Builds a test kernel from that exact upstream commit with generic KASAN, ACPI sleep, and the affected virtio drivers enabled.
2. Applies a **CI-only** patch to the modern virtio-PCI transport. The first `find_vqs()` call for each device succeeds during probe; the next call returns `-ENOMEM`. The guest requests suspend/resume, so the second call is intended to be the failed restore.
3. After resume, the guest unbinds the affected virtio driver. This reaches its remove path after queues were deleted by freeze and restore failed.
4. Captures serial output and checks for a KASAN use-after-free report.

The patch is applied only to the temporary Linux checkout inside the GitHub Actions runner. It does not change the upstream Linux repository or any production kernel. Every test runs in a disposable QEMU guest with TCG emulation and a synthetic virtio-PCI device.

## Covered driver/device pairs

The workflow attempts virtio-input, virtio-net, virtio-snd, virtio-console, virtio-gpu, virtio-crypto, and virtio-CAN. If the installed QEMU build does not provide a requested device model, that matrix entry is marked unsupported instead of being counted as a pass.

## Interpretation

- `KASAN_REPRODUCED`: KASAN reported a use-after-free in the tested guest after the injected restore failure and subsequent unbind.
- `RESTORE_FAILURE_INJECTED_NO_KASAN_REPORT`: the failed restore path ran and unbind completed, but this run did not produce a KASAN UAF report. This is not proof the source-level issue is absent.
- `RESTORE_FAILURE_NOT_OBSERVED`: the guest did not reach the intended failure path; treat the run as inconclusive.
- `UNSUPPORTED`: the runner's QEMU does not expose the requested device model or the device did not bind.

The injection is deliberately limited to the modern **PCI** transport. It does not validate the virtio-MMIO duplicate-IRQ teardown warnings, the virtio-I2C `ERR_PTR` path, or transports not exercised by these QEMU models. It is a runtime confidence step, not a blanket proof of exploitability or severity.
