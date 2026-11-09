from __future__ import annotations

import json
import random
import socket
import struct
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .hosts import valid_public_ip


# 国内可稳定访问的加密/明文 DNS；不依赖 GitHub 自身的域名解析。
DOH_ENDPOINTS = {
    "AliDNS-DoH": "https://dns.alidns.com/resolve",
    "DNSPod-DoH": "https://doh.pub/dns-query",
}
UDP_SERVERS = {
    "AliDNS-UDP": "223.5.5.5",
    "DNSPod-UDP": "119.29.29.29",
    "114DNS-UDP": "114.114.114.114",
}


@dataclass
class Resolution:
    host: str
    by_source: dict[str, list[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ips(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        # 先使用国内 DNS 的结果，再补充其他来源。
        for source in sorted(self.by_source):
            for ip in self.by_source[source]:
                if ip not in seen:
                    seen.add(ip)
                    result.append(ip)
        return result

    def sources_for(self, ip: str) -> list[str]:
        return [s for s, ips in self.by_source.items() if ip in ips]


def _build_query(host: str, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(part)]) + part.encode("idna") for part in host.rstrip(".").split("."))
    return header + qname + b"\x00" + struct.pack(">HH", 1, 1)


def _read_name(packet: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    if depth > 12:
        raise ValueError("DNS compression loop")
    labels: list[str] = []
    while True:
        if offset >= len(packet):
            raise ValueError("DNS packet truncated")
        length = packet[offset]
        if length == 0:
            return ".".join(labels), offset + 1
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("DNS pointer truncated")
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            tail, _ = _read_name(packet, pointer, depth + 1)
            if tail:
                labels.append(tail)
            return ".".join(labels), offset + 2
        offset += 1
        labels.append(packet[offset : offset + length].decode("ascii", "replace"))
        offset += length


def udp_query(host: str, server: str, timeout: float = 2.5) -> list[str]:
    qid = random.randint(0, 0xFFFF)
    packet = _build_query(host, qid)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)
    if len(data) < 12:
        raise ValueError("DNS response too short")
    rid, flags, qdcount, ancount, _, _ = struct.unpack(">HHHHHH", data[:12])
    if rid != qid:
        raise ValueError("DNS transaction id mismatch")
    if flags & 0x0200:
        raise ValueError("DNS response truncated")
    offset = 12
    for _ in range(qdcount):
        _, offset = _read_name(data, offset)
        offset += 4
    ips: list[str] = []
    for _ in range(ancount):
        _, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlength]
        offset += rdlength
        if rtype == 1 and rclass == 1 and len(rdata) == 4:
            ip = socket.inet_ntoa(rdata)
            if valid_public_ip(ip):
                ips.append(ip)
    return list(dict.fromkeys(ips))


def doh_query(host: str, endpoint: str, timeout: float = 4.0) -> list[str]:
    query = urllib.parse.urlencode({"name": host, "type": "A"})
    sep = "&" if "?" in endpoint else "?"
    req = urllib.request.Request(
        endpoint + sep + query,
        headers={"Accept": "application/dns-json", "User-Agent": "GitHubFix/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read(512 * 1024).decode("utf-8", "replace"))
    ips: list[str] = []
    for answer in payload.get("Answer", []) or []:
        if int(answer.get("type", 0)) == 1:
            ip = str(answer.get("data", "")).strip()
            if valid_public_ip(ip):
                ips.append(ip)
    return list(dict.fromkeys(ips))


def resolve(host: str, *, timeout: float = 5.0, include_system: bool = False) -> Resolution:
    result = Resolution(host=host)
    if include_system:
        try:
            infos = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
            system_ips = list(dict.fromkeys(info[4][0] for info in infos if valid_public_ip(info[4][0])))
            if system_ips:
                result.by_source["系统解析"] = system_ips
        except OSError as exc:
            result.errors["系统解析"] = str(exc)

    tasks: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, server in UDP_SERVERS.items():
            tasks[pool.submit(udp_query, host, server, min(2.5, timeout))] = name
        for name, endpoint in DOH_ENDPOINTS.items():
            tasks[pool.submit(doh_query, host, endpoint, timeout)] = name
        deadline = time.monotonic() + timeout + 0.5
        try:
            iterator = as_completed(tasks, timeout=max(0.1, deadline - time.monotonic()))
            for future in iterator:
                name = str(tasks[future])
                try:
                    ips = future.result()
                except Exception as exc:
                    result.errors[name] = str(exc)
                    continue
                if ips:
                    result.by_source[name] = ips
        except TimeoutError:
            result.errors["解析超时"] = "部分 DNS 服务器响应超时，已使用其余结果"
    return result


def resolve_many(hosts: list[str], *, timeout: float = 5.0) -> dict[str, Resolution]:
    with ThreadPoolExecutor(max_workers=min(10, len(hosts) or 1)) as pool:
        futures = {pool.submit(resolve, host, timeout=timeout): host for host in hosts}
        output: dict[str, Resolution] = {}
        for future in as_completed(futures):
            host = futures[future]
            try:
                output[host] = future.result()
            except Exception as exc:
                res = Resolution(host=host)
                res.errors["解析器"] = str(exc)
                output[host] = res
        return output
