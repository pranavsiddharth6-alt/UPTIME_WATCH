import re
import socket
import ssl
import time
from functools import lru_cache
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

import certifi
import dns.exception
import dns.resolver

CHECK_SECONDS = 5
DNS_SECONDS = 2
MAX_HEADER_BYTES = 32 * 1024
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
PATH = re.compile(r"(?:/(?:[a-zA-Z0-9._~!$&'()*+,;=:@/\-]|%[a-fA-F0-9]{2})*)?")
INTERNAL_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".lan", ".corp",
    ".invalid", ".test", ".example", ".onion", ".arpa",
)
IPV6_UNICAST = ip_network("2000::/3")
SPECIAL_IPV4 = (
    ip_network("168.63.129.16/32"),  # Azure platform address
    ip_network("192.0.0.0/24"),
    ip_network("192.88.99.0/24"),
)
SPECIAL_IPV6 = (
    ip_network("2001::/23"),
    ip_network("2001:db8::/32"),
    ip_network("2002::/16"),
    ip_network("3fff::/20"),
)


class UnsafeTarget(ValueError):
    pass


class ProbeUnavailable(Exception):
    pass


class SecureTransportUnavailable(Exception):
    pass


def normalize_url(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or any(not 33 <= ord(char) <= 126 for char in value)
        or any(char in value for char in "\\?#")
    ):
        raise UnsafeTarget()

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        raise UnsafeTarget() from None

    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.netloc.lower() not in (hostname, f"{hostname}:443")
        or not _public_hostname(hostname)
        or not PATH.fullmatch(parsed.path)
    ):
        raise UnsafeTarget()

    path = "" if parsed.path == "/" else parsed.path
    return f"https://{hostname}{path}"


def _public_hostname(hostname):
    labels = hostname.split(".")
    return (
        len(hostname) <= 253
        and len(labels) >= 2
        and labels[-1][0:1].isalpha()
        and not hostname.endswith(INTERNAL_SUFFIXES)
        and all(HOST_LABEL.fullmatch(label) for label in labels)
    )


def _public_address(address):
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if address.version == 6:
        return address in IPV6_UNICAST and not any(address in net for net in SPECIAL_IPV6)
    return not any(address in net for net in SPECIAL_IPV4)


def _remaining(deadline):
    seconds = deadline - time.perf_counter()
    if seconds <= 0:
        raise ProbeUnavailable()
    return seconds


def _resolve_public_ip(hostname, deadline):
    addresses = []
    try:
        for record_type in ("A", "AAAA"):
            answers = dns.resolver.resolve(
                f"{hostname}.", record_type, lifetime=_remaining(deadline),
                search=False, raise_on_no_answer=False,
            )
            for answer in answers:
                address = ip_address(answer.address)
                if not _public_address(address):
                    raise UnsafeTarget()
                addresses.append(address)
    except dns.exception.DNSException:
        raise ProbeUnavailable() from None

    _remaining(deadline)
    if not addresses:
        raise ProbeUnavailable()
    return addresses[0]


def validate_public_dns(url):
    hostname = urlsplit(normalize_url(url)).hostname
    _resolve_public_ip(hostname, time.perf_counter() + DNS_SECONDS)


@lru_cache(maxsize=1)
def _tls_context():
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=certifi.where())
        return context
    except (OSError, RuntimeError):
        raise SecureTransportUnavailable() from None


def _read_http_status(connection, deadline):
    headers = bytearray()
    bytes_read = 0
    while bytes_read < MAX_HEADER_BYTES:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(min(4096, MAX_HEADER_BYTES - bytes_read))
        if not chunk:
            break
        bytes_read += len(chunk)
        headers.extend(chunk)

        while (end := headers.find(b"\r\n\r\n")) != -1:
            status_line = headers.split(b"\r\n", 1)[0]
            match = re.fullmatch(rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [^\r\n]*)?", status_line)
            if not match:
                raise ProbeUnavailable()
            status = int(match[1])
            if status >= 200 or status == 101:
                return status
            del headers[:end + 4]
    raise ProbeUnavailable()


def _probe_https(context, url, hostname, address, deadline):
    if not _public_address(address):
        raise UnsafeTarget()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise SecureTransportUnavailable()

    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    # Resolve the numeric literal only. The registered hostname goes to Host and SNI.
    endpoint = socket.getaddrinfo(
        str(address), 443, family, socket.SOCK_STREAM, socket.IPPROTO_TCP,
        socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
    )[0][4]
    if ip_address(endpoint[0]) != address:
        raise SecureTransportUnavailable()
    path = urlsplit(url).path or "/"
    request_bytes = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {hostname}\r\n"
        "User-Agent: UptimeWatch/1.0\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    with socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP) as connection:
        connection.settimeout(min(2, _remaining(deadline)))
        connection.connect(endpoint)
        connection.settimeout(_remaining(deadline))
        with context.wrap_socket(connection, server_hostname=hostname) as tls:
            tls.settimeout(_remaining(deadline))
            tls.sendall(request_bytes)
            return _read_http_status(tls, deadline)


def check_website(url):
    url = normalize_url(url)
    started_at = time.perf_counter()
    context = _tls_context()
    hostname = urlsplit(url).hostname
    try:
        address = _resolve_public_ip(hostname, started_at + DNS_SECONDS)
        http_status = _probe_https(context, url, hostname, address, started_at + CHECK_SECONDS)
    except (OSError, ProbeUnavailable):
        http_status = None

    online = http_status is not None and 200 <= http_status < 300
    return {
        "url": url,
        "status": "online" if online else "offline",
        "http_status": http_status,
        "response_time_ms": round((time.perf_counter() - started_at) * 1000, 2),
    }
