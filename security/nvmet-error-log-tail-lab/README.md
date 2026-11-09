# NVMe/TCP Error Log tail disclosure lab

This directory contains an isolated regression experiment for the Linux NVMe target Error Information Log Page (LID 0x01).

## Source-level path

The source audit verifies the following path in upstream Linux:

1. `nvmet_admin_cmd_data_len()` derives the transfer size from the host's NUMD field; it does not cap an Error Log request at the fixed log length.
2. `nvmet_tcp_map_data()` allocates an SGL for the requested transfer size.
3. `sgl_alloc_order()` allocates the backing pages with `alloc_pages(gfp, order)`. The NVMe/TCP caller supplies `GFP_KERNEL | __GFP_NOWARN`, not `__GFP_ZERO`.
4. `nvmet_execute_get_log_page_error()` copies at most 128 × 64 bytes (8192 bytes) and completes success without initializing a larger transfer tail.
5. `nvmet_setup_c2h_data_pdu()` describes the full requested transfer and `nvmet_try_send_data()` sends the scatterlist through `MSG_SPLICE_PAGES`.

The target advertises ELPE=127 (128 slots). The oversized tail is therefore beyond the complete valid Error Information Log. The source path was checked in upstream tags v5.0, v5.4, v5.10, v5.15, v6.1, v6.6, v6.12, v6.18, v7.0, and current master. The original Error Log handler was introduced in December 2018 by commit `11ad507784ed`.

## Partial-read compatibility

Upstream intentionally supports short Error Log reads. Commit `5f8badbcbeac` changed the handler to return as many entries as fit instead of failing when the supplied transfer buffer is shorter than the full log.

The candidate fix zeroes the complete response SGL before copying any Error Log slots. This preserves the original short-read behavior exactly: the existing loop still copies as many complete or partial bytes as fit and completes successfully. Zeroing takes place before acquiring `error_lock`, and a zeroing failure is returned immediately.

The regression matrix includes 4, 64, 68, 4096, 8188, 8192, 8196, 16384, and 65536-byte transfers. The short and boundary lengths check protocol compatibility; oversized requests check tail initialization.

## Runtime probes

- `run-loopback.sh` configures a disposable target and connects only to 127.0.0.1.
- `probe.c` uses the local NVMe admin ioctl and emits aggregate tail statistics only.
- `raw_tcp_probe.py` implements the NVMe/TCP handshake and Get Log Page directly over a TCP socket. It is run as `nobody` with no effective capabilities.
- The userspace `0xA5` spray is best-effort only. It does not guarantee the target allocator reuses those pages. The absence of the marker is not proof that the tail is initialized.

## KMSAN send-boundary check

The KMSAN A/B workflow applies `kmsan-tail-check.patch` to the test kernel. This adds `kmsan_check_memory()` for the oversized Error Log tail immediately before NVMe/TCP hands the relevant page range to `sock_sendmsg(MSG_SPLICE_PAGES)`.

KMSAN marks pages allocated without `__GFP_ZERO` as uninitialized and stores an allocation-origin stack. A report at this boundary should identify the allocation provenance. The test-only instrumentation is diagnostic and is not part of the proposed production fix.

Interpretation:
- An uninitialized-memory report on the unpatched kernel, with an origin stack reaching the SGL page allocation path, is strong direct evidence.
- The same report absent after the candidate zero-tail fix, with short-read tests still passing, validates the mitigation.
- No report is inconclusive; non-zero bytes alone do not establish that their contents are sensitive kernel data.

## Upstream precedent

On 2026-11-04, upstream commit `29b8758b4700` fixed the analogous ANA Log Page issue by zeroing the complete response SGL before copying the valid ANA structures. The Error Log handler predates that fix and has a distinct fixed-size payload, so it needs its own validation.

## Reachability and safety

The raw unprivileged protocol test demonstrates command acceptance only against this disposable loopback target configured with `allow_any_host=1`. It does not establish remote unauthenticated reachability or production host-authentication policy.

No production endpoint is contacted. Raw response bytes are not printed, saved, or uploaded. This is a reproducer and candidate mitigation, not a claim that sensitive data has been recovered.
