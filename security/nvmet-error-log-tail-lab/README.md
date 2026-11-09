# NVMe/TCP Error Log tail disclosure lab

This directory contains a localhost-only regression experiment for the Linux NVMe target Error Information Log Page (LID 0x01).

## What the source audit establishes

The current upstream path has three relevant steps:

1. `nvmet_tcp_map_data()` takes the requested SGL length and allocates the response pages with `sgl_alloc(len, GFP_KERNEL | __GFP_NOWARN, ...)`.
2. `sgl_alloc_order()` obtains each payload page with `alloc_pages(gfp, 0)`. The scatterlist metadata is initialized, but this call does not request `__GFP_ZERO`.
3. `nvmet_execute_get_log_page_error()` copies the fixed 128 × 64-byte Error Information Log (8192 bytes). In the unpatched implementation it completes successfully without writing the rest of a larger transfer. NVMe/TCP then sends the complete `req.transfer_len` using `MSG_SPLICE_PAGES`.

This gives a precise candidate path for allocator-resident bytes to reach the host. It does not, by itself, establish that naturally allocated tail bytes are sensitive or disclose another user's data.

## Runtime probes

- `run-loopback.sh` configures a disposable target backed by a temporary loop device, connects only to 127.0.0.1, and checks 8 KiB, 16 KiB, and 64 KiB requests.
- `probe.c` reports aggregate tail statistics only. It never writes raw response bytes to disk or uploads them as artifacts.
- The userspace `0xA5` spray is best-effort only. It does not guarantee that the target's later `alloc_pages()` calls reuse those exact pages. A marker match is useful evidence; a non-match says little about allocator provenance.
- No `page_spray.ko` module is currently built or loaded by the KMSAN guest. Earlier README wording that implied it was loaded was inaccurate.

## KMSAN send-boundary check

The KMSAN A/B workflow applies `kmsan-tail-check.patch` to the test kernel before either run. This test-only instrumentation calls `kmsan_check_memory()` on the Error Log response tail immediately before NVMe/TCP passes that range to `sock_sendmsg(MSG_SPLICE_PAGES)`.

This matters because page-splice send paths can transfer page references rather than performing an ordinary instrumented byte copy. The explicit check verifies KMSAN metadata at the actual transport boundary and should retain the allocation origin in a report.

Interpretation:

- A KMSAN report in `nvmet_try_send_data()` on the unpatched kernel confirms that bytes in the oversized Error Log tail are still marked uninitialized at the send boundary. The KMSAN origin stack can then identify the allocation path.
- If the candidate zero-tail patch removes that report and the returned tail is all zero, that is strong evidence the patch closes this specific path.
- If neither variant reports, the natural allocation bytes were not shown to be uninitialized by this run. Do not label the result a confirmed disclosure on that evidence alone.
- The check patch is diagnostic instrumentation for this lab, not a production fix.

## Reachability and safety scope

The raw unprivileged protocol probe implements the NVMe/TCP handshake, Fabrics Connect, Property Set(CC), and Get Log Page directly over a TCP socket. It runs as `nobody`, with no effective capabilities, and contacts only the disposable loopback target. It demonstrates command acceptance in that test configuration; it does not establish that a production target accepts arbitrary host NQNs or is reachable by a remote attacker.

No production NVMe endpoint is contacted. The target namespace is a temporary 64 MiB RAM/loop-backed image. Raw response bytes are summarized and are not printed or saved.
