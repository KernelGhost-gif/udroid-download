# NVMe/TCP Error Log tail disclosure lab

This directory contains a deliberately narrow, localhost-only regression experiment for the Linux NVMe target Error Information Log Page (LID 0x01).

## What it checks

1. check_source.py asserts the current upstream source relationships behind the candidate:
   - 128 target Error Log entries, each 64 bytes (8 KiB total);
   - the Error Log handler copies those entries but does not clear a larger requested response tail;
   - NVMe/TCP allocates a response scatterlist for the requested descriptor length and sends the full transfer length;
   - the scatterlist page allocator does not request __GFP_ZERO.
2. run-loopback.sh configures a temporary nvmet subsystem backed by a disposable loop device, connects an NVMe/TCP initiator to 127.0.0.1, and issues 8 KiB, 16 KiB, and 64 KiB Get Log Page requests.
3. probe.c reports only aggregate tail statistics. It never writes response bytes to disk or uploads them as artifacts. Before oversized requests it makes a best-effort attempt to return pages filled with a harmless 0xA5 marker to the page allocator.

## Interpreting results

- TEST_MARKER_REAPPEARED: at least 64 consecutive marker bytes appeared beyond byte 8191. This is strong evidence that the response tail was not overwritten and that the allocator reused the test pages.
- NONZERO_TAIL_OBSERVED: the tail contained non-zero bytes, but the marker was not reproduced. This is evidence of a non-zero tail, not by itself proof that the bytes are sensitive kernel data.
- ZERO_TAIL_INCONCLUSIVE: the response tail was all zero. This does not disprove the source-level issue; allocator zeroing or page reuse may mask it.
- REQUEST_REJECTED: the command was rejected. Inspect the command status and runner logs before drawing conclusions.
- RUNTIME_UNSUPPORTED: the GitHub-hosted runner did not expose the required kernel modules or configfs/loop-device support. The source audit still runs independently.

The standard Linux host ioctl path restricts admin passthrough to privileged callers. This lab uses root inside a disposable GitHub-hosted runner and connects only to loopback. It does not test exposure to an unauthenticated remote host.

## Safety and scope

- No production target or external NVMe endpoint is contacted.
- The target namespace is a temporary 64 MiB image on a loop device.
- allow_any_host is enabled only on the disposable loopback subsystem.
- Raw response data is never logged or uploaded.
- This is a reproduction harness, not a claim that a vulnerability has been confirmed. KMSAN or a controlled allocator reproduction would still be useful to establish uninitialized-memory provenance.


## Expanded parallel test matrix

The workflow now runs the following independent jobs:

- Source audit against current upstream Linux.
- Hosted-runner NVMe/TCP loopback baseline.
- QEMU comparison with page auto-initialization explicitly disabled and enabled.
- Candidate-fix application, source assertions, and compilation of the changed NVMe target object.
- A KMSAN-enabled x86_64 kernel booted in QEMU. The unpatched handler is tested first; the candidate fix is then applied and the same KMSAN guest is run again.

The KMSAN guest loads page_spray.ko and triggers its sysfs parameter immediately before the probe. The helper fills and releases order-0 pages with 0xA5 to increase the chance that nvmet's response SGL reuses those pages. The probe records aggregate tail statistics only; it does not write returned bytes to files or Actions artifacts.

## KMSAN A/B interpretation

- A KMSAN report whose stack includes nvmet TCP response functions on the unpatched kernel is direct evidence that uninitialized response bytes reached a use site.
- The same signal disappearing after the tail-zeroing patch is evidence that the candidate fix addresses that path.
- A non-zero or marker tail before the fix, absent after the fix, is also a useful runtime result.
- No report and all-zero tails in both variants remain inconclusive. They do not prove the path is safe.

KMSAN is diagnostic, not a proof by itself. The report stack and exact upstream Linux commit must be retained with the run output.


## Raw unprivileged NVMe/TCP protocol test

raw_tcp_probe.py implements the NVMe/TCP ICReq/ICResp handshake, Fabrics Connect,
Property Set(CC), and Get Log Page directly over a TCP socket. The loopback
runner invokes it as the unprivileged nobody account, against 127.0.0.1 only.
It does not open /dev/nvme*, use an ioctl, or require CAP_SYS_ADMIN.

This test is intended to answer a narrower reachability question: can an
unprivileged local process speak the NVMe/TCP protocol and request an oversized
Error Information Log from a target configured to accept the test host NQN?
The disposable target has allow_any_host enabled only for this isolated test.
It does not establish whether a production target allows arbitrary host NQNs,
whether a network attacker can reach the port, or whether host authentication
is disabled in a real deployment.

A successful raw Connect and Get Log Page proves protocol-level command
acceptance without the Linux host driver's admin ioctl permission gate. Tail
bytes are summarized and never printed or saved.
