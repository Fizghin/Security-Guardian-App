"""
Phones as cameras.

A phone opens https://<this computer>:<PHONE_PORT>/phone?k=<token> in its
browser and sends JPEG frames to /api/phone/frame. Every reply carries the
commands queued for that phone (speak a warning, sound the siren), so the
phone can also be the loudspeaker at the spot it watches.

Browsers only allow camera access on HTTPS, so this module also creates a
self-signed certificate for this computer's local addresses. The phone shows
a one-time warning for it, because the certificate is not from a public
authority; the connection is still encrypted.
"""
import ipaddress
import socket
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import psutil
import segno

from config import DATA_DIR, PHONE_PORT
from services.sources import PhoneSource

TLS_DIR = DATA_DIR / "tls"
CERT_FILE = TLS_DIR / "guardian.crt"
KEY_FILE = TLS_DIR / "guardian.key"
ONLINE_SECONDS = 6.0


def lan_addresses() -> list[str]:
    """This computer's IPv4 addresses that a phone on the same network can reach."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; this picks the default interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET:
                found.append(a.address)
    out = []
    for ip in found:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or ip in out:
            continue
        out.append(ip)
    # Private (home network) addresses first; sort is stable so the default interface stays first.
    return sorted(out, key=lambda ip: not ipaddress.ip_address(ip).is_private)


def ensure_certificate() -> tuple[str, str]:
    """Self-signed certificate covering localhost and the current LAN addresses."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ips = lan_addresses()
    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            cert = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            covered = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
            fresh = cert.not_valid_after_utc - datetime.now(timezone.utc) > timedelta(days=30)
            if fresh and set(ips) <= covered:
                return str(CERT_FILE), str(KEY_FILE)
        except Exception:
            pass

    TLS_DIR.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    hostname = socket.gethostname() or "guardian"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Guardian on {hostname}"[:64])])
    alt = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    if hostname and hostname != "localhost":
        alt.append(x509.DNSName(hostname))
    alt += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=800))  # iOS rejects TLS certificates valid for more than 825 days
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[phone] Created HTTPS certificate for {', '.join(['localhost'] + ips)}")
    return str(CERT_FILE), str(KEY_FILE)


def pairing_urls(token: str) -> list[str]:
    return [f"https://{ip}:{PHONE_PORT}/phone?k={token}" for ip in lan_addresses()]


def qr_svg(text: str) -> str:
    return segno.make(text, error="m").svg_inline(scale=6, border=2, dark="#000", light="#fff")


class PhoneLink:
    def __init__(self, camera_id: str, token: str):
        self.camera_id = camera_id
        self.token = token
        self.source = PhoneSource()
        self.commands: deque = deque(maxlen=20)
        self.siren_until = 0.0

    @property
    def online(self) -> bool:
        return time.time() - self.source.last_contact < ONLINE_SECONDS


class PhoneHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._links: dict[str, PhoneLink] = {}

    def register(self, camera_id: str, token: str) -> PhoneSource:
        with self._lock:
            link = self._links.get(camera_id)
            if link is None:
                link = self._links[camera_id] = PhoneLink(camera_id, token)
            link.token = token
            return link.source

    def unregister(self, camera_id: str) -> None:
        with self._lock:
            self._links.pop(camera_id, None)

    def authenticate(self, token: str) -> PhoneLink | None:
        if not token:
            return None
        with self._lock:
            for link in self._links.values():
                if link.token == token:
                    return link
        return None

    def send(self, camera_id: str, command: dict) -> bool:
        """Queue a command for the phone. Returns False when the phone is not reachable."""
        with self._lock:
            link = self._links.get(camera_id)
        if link is None or not link.online:
            return False
        if command.get("type") == "siren":
            link.siren_until = time.time() + command.get("seconds", 60) if command.get("on") else 0.0
        link.commands.append(command)
        return True

    @staticmethod
    def take_commands(link: PhoneLink) -> list[dict]:
        out = []
        while link.commands:
            out.append(link.commands.popleft())
        return out


phone_hub = PhoneHub()
