#!/usr/bin/env python3
"""Inject a one-shot second-find_vqs failure into a disposable Linux checkout."""
from __future__ import annotations

from pathlib import Path
import sys


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"TEST_INJECTION_ERROR={label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <linux-source-tree>", file=sys.stderr)
        return 64
    root = Path(sys.argv[1])
    modern = root / "drivers/virtio/virtio_pci_modern.c"
    common = root / "drivers/virtio/virtio_pci_common.c"

    source = modern.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "#include <linux/delay.h>\n",
        "#include <linux/delay.h>\n#include <linux/errno.h>\n#include <linux/init.h>\n#include <linux/kernel.h>\n#include <linux/string.h>\n",
        "modern-includes",
    )
    setup = r'''
/* CI-only: fail the second modern-PCI find_vqs call (the restore attempt). */
static bool virtio_pci_ci_fail_restore_find_vqs;
static unsigned int virtio_pci_ci_find_vqs_calls;

static int __init virtio_pci_ci_fail_restore_setup(char *str)
{
	if (str && !strcmp(str, "1")) {
		virtio_pci_ci_fail_restore_find_vqs = true;
		return 1;
	}
	return 0;
}
__setup("virtio_pci_ci_fail_restore_find_vqs=",
	virtio_pci_ci_fail_restore_setup);
'''
    source = replace_once(
        source,
        '#include "virtio_pci_common.h"\n',
        '#include "virtio_pci_common.h"\n' + setup,
        "boot-parameter-injection",
    )
    call = "\tint rc = vp_find_vqs(vdev, nvqs, vqs, vqs_info, desc);"
    replacement = r'''	int rc;

	if (virtio_pci_ci_fail_restore_find_vqs &&
	    virtio_pci_ci_find_vqs_calls++) {
		dev_warn(&vdev->dev,
			 "VIRTIO_RESTORE_KASAN_TEST_FAIL: injected -ENOMEM from find_vqs\n");
		return -ENOMEM;
	}

	rc = vp_find_vqs(vdev, nvqs, vqs, vqs_info, desc);'''
    source = replace_once(source, call, replacement, "second-find-vqs-failure")
    modern.write_text(source, encoding="utf-8")

    source = common.read_text(encoding="utf-8")
    old_pm = r'''static int virtio_pci_suspend(struct device *dev)
{
	return vp_supports_pm_no_reset(dev) ? 0 : virtio_pci_freeze(dev);
}

static int virtio_pci_resume(struct device *dev)
{
	return vp_supports_pm_no_reset(dev) ? 0 : virtio_pci_restore(dev);
}'''
    new_pm = r'''static int virtio_pci_suspend(struct device *dev)
{
	bool no_reset = vp_supports_pm_no_reset(dev);

	dev_info(dev, "VIRTIO_RESTORE_KASAN_TEST_FORCE_FREEZE no_soft_reset=%d\n",
		 no_reset);
	return virtio_pci_freeze(dev);
}

static int virtio_pci_resume(struct device *dev)
{
	dev_info(dev, "VIRTIO_RESTORE_KASAN_TEST_FORCE_RESTORE\n");
	return virtio_pci_restore(dev);
}'''
    source = replace_once(source, old_pm, new_pm, "force-transport-freeze-restore")
    common.write_text(source, encoding="utf-8")

    print("TEST_INJECTION=APPLIED")
    print("TEST_INJECTION=first find_vqs succeeds; second returns -ENOMEM")
    print("TEST_INJECTION=PCI suspend/resume forced through virtio freeze/restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
