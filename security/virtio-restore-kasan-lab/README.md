# Virtio failed-restore KASAN lab

This is an isolated runtime-validation harness for the source-level stale-virtqueue lifetime paths audited at upstream Linux commit `20eef29b7c8465ac98d7dd6d6f2ac07a8d8056ca`.

## What the test does

1. Builds an x86-64 test kernel from that exact Linux commit with generic KASAN, ACPI sleep, and the relevant virtio drivers enabled.
2. Applies a source-anchored test transformation to the **temporary Linux checkout inside the Actions runner**. It asserts each expected upstream code anchor occurs exactly once.
3. The test-only transformation lets initial PCI queue setup succeed, injects `-ENOMEM` on the next `find_vqs()` call, and forces the virtio-PCI PM callbacks through `virtio_device_freeze()` and `virtio_device_restore()`. Forcing these callbacks avoids relying on the device's `NO_SOFT_RESET` capability bit to decide whether the test path runs.
4. Boots one QEMU TCG guest per driver, requests ACPI S3 sleep, resumes through QMP, and unbinds the driver after failed restore. This exercises the driver's normal remove/cleanup path against the state left by failed restore.
5. Captures serial/QEMU logs and checks for KASAN use-after-free reports.

No upstream Linux source is changed or committed. Each test uses an isolated synthetic virtio-PCI device in a disposable QEMU guest.

## Covered driver/device pairs

The workflow attempts virtio-input, virtio-net, virtio-snd, virtio-console, virtio-gpu, virtio-crypto, and virtio-CAN. Device models or backends absent from the hosted runner's QEMU build are marked unsupported rather than counted as passes. CAN Kconfig dependencies are explicitly enabled and validated at kernel configuration time.

## Interpreting results

- `KASAN_REPRODUCED`: KASAN reported a use-after-free after the injected restore failure and subsequent unbind.
- `RESTORE_FAILURE_INJECTED_NO_KASAN_REPORT`: the restore failure marker was observed and unbind completed, but this run did not produce a KASAN UAF report. This is a non-reproduction, not proof the source path is safe.
- `RESTORE_FAILURE_NOT_OBSERVED` or a failed guest: the intended failure path was not confirmed; treat the run as inconclusive.
- `UNSUPPORTED`: QEMU lacks the requested device model/backend or the target did not bind.

This test covers the modern virtio-PCI transport for the listed devices. It does not validate the virtio-MMIO duplicate-IRQ teardown warnings, the separate virtio-I2C `ERR_PTR` path, or PCI transports not represented in the QEMU matrix. It is runtime evidence, not a blanket proof of exploitability or severity.
