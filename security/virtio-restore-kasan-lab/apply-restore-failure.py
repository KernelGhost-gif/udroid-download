#!/usr/bin/env python3
"""Apply test-only virtio-PCI find_vqs failure injection to a kernel checkout."""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <linux-source-tree>")

source = Path(sys.argv[1]) / "drivers/virtio/virtio_pci_modern.c"
text = source.read_text()

if "VIRTIO_RESTORE_KASAN_TEST_FAIL" in text:
    raise SystemExit("test injection is already present")

def replace_once(old: str, new: str, description: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"expected exactly one {description} anchor in {source}, found {count}"
        )
    text = text.replace(old, new, 1)

replace_once(
    "#include <linux/delay.h>\n#include <linux/virtio_pci_admin.h>",
    "#include <linux/delay.h>\n"
    "#include <linux/init.h>\n"
    "#include <linux/kernel.h>\n"
    "#include <linux/string.h>\n"
    "#include <linux/virtio_pci_admin.h>",
    "include block",
)

replace_once(
    '#include "virtio_pci_common.h"\n\n#define VIRTIO_AVQ_SGS_MAX\t4',
    '#include "virtio_pci_common.h"\n\n'
    "/*\n"
    " * CI-only injection: this guest has exactly one virtio-PCI device.\n"
    " * Allow initial probe, then fail the next find_vqs() call (restore).\n"
    " */\n"
    "static bool virtio_pci_ci_fail_restore_find_vqs;\n"
    "static unsigned int virtio_pci_ci_find_vqs_calls;\n"
    "\n"
    "static int __init virtio_pci_ci_fail_restore_setup(char *str)\n"
    "{\n"
    '\tif (str && !strcmp(str, "1")) {\n'
    "\t\tvirtio_pci_ci_fail_restore_find_vqs = true;\n"
    "\t\treturn 1;\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
    '__setup("virtio_pci_ci_fail_restore_find_vqs=",\n'
    "\tvirtio_pci_ci_fail_restore_setup);\n"
    "\n"
    "#define VIRTIO_AVQ_SGS_MAX\t4",
    "virtio PCI boot-parameter block",
)

replace_once(
    "\tint rc = vp_find_vqs(vdev, nvqs, vqs, vqs_info, desc);",
    "\tint rc;\n"
    "\n"
    "\tif (virtio_pci_ci_fail_restore_find_vqs &&\n"
    "\t    virtio_pci_ci_find_vqs_calls++) {\n"
    "\t\tdev_warn(&vdev->dev,\n"
    '\t\t\t "VIRTIO_RESTORE_KASAN_TEST_FAIL: injected -ENOMEM from find_vqs\\n");\n'
    "\t\treturn -ENOMEM;\n"
    "\t}\n"
    "\n"
    "\trc = vp_find_vqs(vdev, nvqs, vqs, vqs_info, desc);",
    "vp_modern_find_vqs call",
)

source.write_text(text)
print(f"APPLIED_CI_TEST_INJECTION={source}")
