#!/usr/bin/env python3
"""Validate candidate fix while preserving short/partial Error Log reads."""
from pathlib import Path
import sys


def main(root: str) -> int:
    src = Path(root, "drivers/nvme/target/admin-cmd.c").read_text()
    marker = "static void nvmet_execute_get_log_page_error"
    start = src.index(marker)
    end = src.index("\nstatic void nvmet_execute_get_supported_log_pages", start)
    body = src[start:end]
    checks = {
        "copies no more than one slot per iteration":
            "sizeof(struct nvme_error_slot)" in body and "copy_len" in body,
        "preserves partial and short reads":
            "min_t(size_t, sizeof(struct nvme_error_slot)" in body and
            "req->transfer_len - offset" in body and
            "if (copy_len < sizeof(struct nvme_error_slot))" in body,
        "copy errors are propagated":
            "status = nvmet_copy_to_sgl" in body and "if (status)" in body,
        "offset advances only by bytes actually copied":
            "offset += copy_len" in body and
            "offset += sizeof(struct nvme_error_slot)" not in body,
        "tail is zeroed after releasing error_lock":
            "spin_unlock_irqrestore" in body and
            "nvmet_zero_sgl" in body and
            body.index("spin_unlock_irqrestore") < body.index("nvmet_zero_sgl"),
        "all remaining response bytes are zeroed":
            "nvmet_zero_sgl(req, offset, req->transfer_len - offset)" in body,
        "completion returns real copy/zero status":
            "nvmet_req_complete(req, status)" in body,
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
