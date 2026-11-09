#!/usr/bin/env python3
"""Validate the minimal zero-first Error Log fix and preserved read semantics."""
from pathlib import Path
import sys


def main(root: str) -> int:
    src = Path(root, "drivers/nvme/target/admin-cmd.c").read_text()
    marker = "static void nvmet_execute_get_log_page_error"
    start = src.index(marker)
    end = src.index(
        "\nstatic void nvmet_execute_get_supported_log_pages", start
    )
    body = src[start:end]
    zero = body.find("status = nvmet_zero_sgl(req, 0, req->transfer_len)")
    lock = body.find("spin_lock_irqsave")
    checks = {
        "entire requested SGL is zeroed":
            zero >= 0,
        "zeroing occurs before the Error Log copy lock":
            zero >= 0 and lock > zero,
        "zeroing failure is returned and processing stops":
            "if (status)" in body and
            "nvmet_req_complete(req, status)" in body and
            "return;" in body[:lock],
        "original slot-copy loop remains unchanged":
            "if (nvmet_copy_to_sgl(req, offset, &ctrl->slots[slot]," in body and
            "sizeof(struct nvme_error_slot)" in body and
            "offset += sizeof(struct nvme_error_slot)" in body,
        "short and partial-slot reads retain original behavior":
            "if (nvmet_copy_to_sgl" in body and
            "nvmet_req_complete(req, 0)" in body,
        "no second tail-clearing path changes copy semantics":
            body.count("nvmet_zero_sgl") == 1,
    }
    for label, ok in checks.items():
        print(f'{"PASS" if ok else "FAIL"}: {label}')
    if not all(checks.values()):
        print("PATCH_REGRESSION=FAIL")
        print("--- inspected handler ---")
        print(body)
        return 1
    print("PATCH_REGRESSION=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "linux"))
