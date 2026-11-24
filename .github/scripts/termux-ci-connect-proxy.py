#!/usr/bin/env python3
"""Loopback-only HTTPS CONNECT proxy for Android emulator ADB reverse.

The Android emulator has no guaranteed outbound network in CI. ADB reverse
lets Termux use this host-side tunnel while the host runner supplies egress.
Only public destination addresses on ports 80 and 443 are allowed.
"""
from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import sys
from urllib.parse import urlsplit


def open_upstream(authority: str) -> socket.socket:
    host, separator, port_text = authority.rpartition(":")
    if not separator or not host:
        raise ValueError("CONNECT target must include a host and port")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    port = int(port_text)
    if port not in (80, 443):
        raise ValueError("Only HTTP and HTTPS destination ports are allowed")

    errors = []
    for family, socktype, proto, _, address in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global:
            continue
        upstream = socket.socket(family, socktype, proto)
        upstream.settimeout(20)
        try:
            upstream.connect(address)
            upstream.settimeout(None)
            return upstream
        except OSError as exc:
            errors.append(str(exc))
            upstream.close()
    raise OSError("Could not connect to a public destination: " + "; ".join(errors))


class ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(15)
        data = bytearray()
        while b"\r\n\r\n" not in data and len(data) < 16384:
            chunk = client.recv(4096)
            if not chunk:
                return
            data.extend(chunk)
        if b"\r\n\r\n" not in data:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return

        header_end = data.index(b"\r\n\r\n") + 4
        first_line = bytes(data[:header_end]).split(b"\r\n", 1)[0].decode(
            "ascii", "replace"
        )
        parts = first_line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 CONNECT Required\r\nConnection: close\r\n\r\n")
            return

        upstream = None
        try:
            upstream = open_upstream(parts[1])
            client.sendall(
                b"HTTP/1.1 200 Connection Established\r\n"
                b"Proxy-Agent: termux-ci-connect-proxy\r\n\r\n"
            )
            pending = bytes(data[header_end:])
            if pending:
                upstream.sendall(pending)
            client.settimeout(None)
            sockets = (client, upstream)
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 180)
                if exceptional or not readable:
                    break
                for source in readable:
                    target = upstream if source is client else client
                    payload = source.recv(65536)
                    if not payload:
                        return
                    target.sendall(payload)
        except (OSError, ValueError) as exc:
            try:
                client.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n"
                )
            except OSError:
                pass
            print(f"{self.client_address[0]} {parts[1]}: {exc}", file=sys.stderr, flush=True)
        finally:
            if upstream is not None:
                upstream.close()


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with ThreadedServer(("127.0.0.1", 3128), ConnectHandler) as server:
        print("Listening on 127.0.0.1:3128 for ADB-reversed CONNECT traffic", flush=True)
        server.serve_forever()
