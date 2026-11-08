#!/usr/bin/env python3
"""Source-level regression assertions for the NVMe/TCP Error Log tail candidate."""
from pathlib import Path
import re
import subprocess
import sys


def block_after(source: str, marker: str) -> str:
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing source marker: {marker}")
    opening = source.find("{", start)
    if opening < 0:
        raise AssertionError(f"missing opening brace after: {marker}")
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[start:pos + 1]
    raise AssertionError(f"unterminated source block: {marker}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main(root: str) -> int:
    base = Path(root)
    admin = (base / "drivers/nvme/target/admin-cmd.c").read_text()
    tcp = (base / "drivers/nvme/target/tcp.c").read_text()
    core = (base / "drivers/nvme/target/core.c").read_text()
    header = (base / "drivers/nvme/target/nvmet.h").read_text()
    nvme_header = (base / "include/linux/nvme.h").read_text()
    scatterlist = (base / "lib/scatterlist.c").read_text()

    commit = subprocess.check_output(
        ["git", "-C", str(base), "rev-parse", "HEAD"], text=True
    ).strip()
    print(f"UPSTREAM_LINUX_COMMIT={commit}")

    slots = re.search(r"#define\s+NVMET_ERROR_LOG_SLOTS\s+(\d+)", header)
    require(slots is not None and int(slots.group(1)) == 128,
            "target advertises 128 Error Log slots")

    error_slot = block_after(nvme_header, "struct nvme_error_slot")
    sizes = {"__le64": 8, "__le32": 4, "__le16": 2, "__u8": 1, "u8": 1}
    total = 0
    fields = re.findall(
        r"^\s*(__le64|__le32|__le16|__u8|u8)\s+\w+\s*(?:\[(\d+)\])?\s*;",
        error_slot, re.MULTILINE
    )
    for ctype, count in fields:
        total += sizes[ctype] * (int(count) if count else 1)
    require(total == 64, f"nvme_error_slot computes to {total} bytes (expected 64)")
    require(total * int(slots.group(1)) == 8192,
            "the fixed Error Log payload is exactly 8192 bytes")

    error_fn = block_after(admin, "static void nvmet_execute_get_log_page_error")
    require("NVMET_ERROR_LOG_SLOTS" in error_fn and
            "sizeof(struct nvme_error_slot)" in error_fn and
            "nvmet_copy_to_sgl" in error_fn,
            "Error Log handler copies the fixed slot array")
    require("nvmet_req_complete(req, 0)" in error_fn and
            "nvmet_zero_sgl" not in error_fn and
            "memset" not in error_fn,
            "Error Log handler completes without clearing a larger transfer tail")

    get_len = block_after(admin, "u32 nvmet_get_log_page_len")
    require("numdu" in get_len and "numdl" in get_len and
            "len *= sizeof(u32)" in get_len,
            "Get Log Page transfer length is derived from NUMD")

    check_len = block_after(core, "bool nvmet_check_transfer_len")
    require("len != req->transfer_len" in check_len,
            "generic transfer check compares requested length to command-derived length")

    tcp_map = block_after(tcp, "static int nvmet_tcp_map_data")
    require("NVMET_TCP_MAXH2CDATA" in tcp_map and
            "cmd->req.sg = sgl_alloc(len" in tcp_map,
            "NVMe/TCP allocates an SGL sized to the requested data descriptor")
    require(re.search(r"#define\s+NVMET_TCP_MAXH2CDATA\s+0x400000", tcp) is not None,
            "NVMe/TCP data descriptor ceiling is 4 MiB")

    c2h = block_after(tcp, "static void nvmet_setup_c2h_data_pdu")
    require("pdu->data_length = cpu_to_le32(cmd->req.transfer_len)" in c2h,
            "C2H data PDU advertises the full requested transfer length")

    send_data = block_after(tcp, "static int nvmet_try_send_data(struct nvmet_tcp_cmd *cmd, bool last_in_batch)")
    require("while (cmd->cur_sg)" in send_data and
            "sock_sendmsg" in send_data,
            "TCP send path walks and transmits the response scatterlist")

    allocator = block_after(scatterlist, "struct scatterlist *sgl_alloc_order")
    require("page = alloc_pages(gfp, order)" in allocator and
            "__GFP_ZERO" not in allocator,
            "scatterlist page allocation does not request GFP_ZERO")

    print("SOURCE_AUDIT=PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "linux"))
    except Exception as exc:
        print(f"SOURCE_AUDIT=FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
