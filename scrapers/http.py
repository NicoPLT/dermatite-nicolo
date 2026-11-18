"""Sessione HTTP "educata" condivisa da tutti gli scraper.

Garanzie:
- una richiesta alla volta per dominio e almeno `min_delay_seconds` tra due richieste
  allo stesso dominio (lock per dominio, thread-safe);
- robots.txt scaricato e rispettato prima di ogni pagina (cache per host);
- retry con backoff esponenziale su errori di rete e 5xx; 429 con rispetto di
  Retry-After; dopo un 429 persistente il dominio è sospeso per l'intera esecuzione;
- 403 ripetuti o pagine di challenge (Cloudflare/Akamai) => dominio BLOCCATO, nessun
  tentativo di aggiramento;
- TLS sempre verificato. Se un server non invia il certificato intermedio, la catena
  viene completata scaricando gli intermedi dall'URL AIA e verificandone la firma fino
  a una radice di `certifi` (mai verify=False).
"""
from __future__ import annotations

import gzip
import logging
import random
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .robots import RobotsPolicy, policy_from_response

log = logging.getLogger("tracker.http")

RETRY_STATUSES = {500, 502, 503, 504}
CHALLENGE_MARKERS = (
    "cf-browser-verification", "challenge-platform", "cf_chl_opt", "just a moment...",
    "attention required! | cloudflare", "access denied</title>", "errors.edgesuite.net",
    "captcha-delivery", "px-captcha", "please verify you are a human", "<title>verifying device",
    "verify you are human", "checking your browser",
)
CHALLENGE_MARKERS_200 = (
    "is using a security service for protection against online attacks", "please enable javascript",
    "<title>verifying device", "just a moment...", "cf-browser-verification", "please verify you are a human",
)


class FetchError(RuntimeError):
    """Errore generico non recuperabile per una singola URL."""


class RobotsDisallowed(FetchError):
    pass


class DomainBlocked(FetchError):
    pass


class DomainRateLimited(FetchError):
    pass


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content: bytes
    headers: dict[str, str]
    elapsed: float

    @property
    def text(self) -> str:
        raw = self.content
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        ctype = self.headers.get("content-type", "")
        m = re.search(r"charset=([\w-]+)", ctype, re.I)
        enc = m.group(1) if m else "utf-8"
        try:
            return raw.decode(enc, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class DomainState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_request: float = 0.0
    consecutive_403: int = 0
    blocked_reason: str | None = None
    rate_limited_reason: str | None = None
    min_delay: float | None = None
    requests: int = 0


def domain_key(host_or_url: str) -> str:
    host = urlsplit(host_or_url).netloc if "//" in host_or_url else host_or_url
    host = host.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


class PoliteSession:
    def __init__(self, http_cfg: dict, *, cache_dir: str | Path | None = None,
                 sleep=time.sleep, clock=time.monotonic, session: requests.Session | None = None):
        self.cfg = http_cfg
        self.user_agent = http_cfg["user_agent"]
        self.robots_token = http_cfg.get("robots_token", "DermoPriceTracker")
        self.min_delay = max(2.0, float(http_cfg.get("min_delay_seconds", 2.5)))
        self.jitter = float(http_cfg.get("jitter_seconds", 0.5))
        self.timeout = (float(http_cfg.get("timeout_connect_seconds", 10)), float(http_cfg.get("timeout_read_seconds", 30)))
        self.max_attempts = int(http_cfg.get("max_attempts", 3))
        self.backoff = float(http_cfg.get("backoff_base_seconds", 4))
        self.max_retry_after = float(http_cfg.get("max_retry_after_seconds", 90))
        self.tls_aia = bool(http_cfg.get("tls_aia_fetch", True))
        self.cache_dir = Path(cache_dir) if cache_dir else Path(__file__).resolve().parent.parent / ".cache"
        self._sleep = sleep
        self._clock = clock
        self._s = session or requests.Session()
        # NB: niente "application/json" nell'Accept di default: PrestaShop risponde 200 con
        # corpo VUOTO se lo trova (verificato su clubfarma.it il 18/11/2026).
        self._s.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9",
        })
        self._domains: dict[str, DomainState] = {}
        self._domains_lock = threading.Lock()
        self._robots: dict[str, RobotsPolicy] = {}
        self._verify: dict[str, Any] = {}

    # ------------------------------------------------------------------ stato dominio
    def state(self, url: str) -> DomainState:
        key = domain_key(url)
        with self._domains_lock:
            st = self._domains.get(key)
            if st is None:
                st = self._domains[key] = DomainState()
            return st

    def set_domain_delay(self, url: str, seconds: float) -> None:
        self.state(url).min_delay = max(self.min_delay, float(seconds))

    def domain_status(self, url: str) -> dict:
        st = self.state(url)
        return {"blocked": st.blocked_reason, "rate_limited": st.rate_limited_reason, "requests": st.requests}

    # ------------------------------------------------------------------ robots
    def robots_policy(self, url: str) -> RobotsPolicy:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        pol = self._robots.get(origin)
        if pol is not None:
            return pol
        try:
            res = self._request(origin + "/robots.txt", check_robots=False, max_attempts=2)
            pol = policy_from_response(res.status, res.text if res.ok else None)
        except (DomainBlocked, DomainRateLimited):
            raise
        except FetchError as exc:
            log.warning("robots.txt non leggibile per %s: %s", origin, exc)
            pol = policy_from_response(None, None)
        if pol.crawl_delay:
            self.set_domain_delay(url, pol.crawl_delay)
        self._robots[origin] = pol
        return pol

    def is_allowed(self, url: str) -> bool:
        return self.robots_policy(url).is_allowed(url, self.robots_token)

    # ------------------------------------------------------------------ API pubblica
    def get(self, url: str, *, check_robots: bool = True, accept: str | None = None,
            timeout: float | None = None, max_attempts: int | None = None) -> FetchResult:
        """GET educato. Solleva RobotsDisallowed / DomainBlocked / DomainRateLimited / FetchError."""
        if check_robots and not self.is_allowed(url):
            raise RobotsDisallowed(f"Vietato da robots.txt: {url}")
        return self._request(url, check_robots=check_robots, accept=accept, timeout=timeout, max_attempts=max_attempts)

    def post_api(self, url: str, data: dict, *, accept: str | None = None, timeout: float | None = None,
                 max_attempts: int | None = None) -> FetchResult:
        """POST verso API documentate (es. Overpass), con lo stesso ritmo educato del GET.

        Non si usa mai per pagine di siti di farmacie: lì si legge solo con GET.
        """
        return self._request(url, check_robots=False, accept=accept, timeout=timeout, max_attempts=max_attempts,
                             post_data=data)

    # ------------------------------------------------------------------ interno
    def _wait_turn(self, st: DomainState) -> None:
        delay = st.min_delay or self.min_delay
        target = st.last_request + delay + random.uniform(0, self.jitter)
        now = self._clock()
        if st.last_request and now < target:
            self._sleep(target - now)

    def _request(self, url: str, *, check_robots: bool, accept: str | None = None,
                 max_attempts: int | None = None, timeout: float | None = None,
                 post_data: dict | None = None) -> FetchResult:
        st = self.state(url)
        if st.blocked_reason:
            raise DomainBlocked(st.blocked_reason)
        if st.rate_limited_reason:
            raise DomainRateLimited(st.rate_limited_reason)
        attempts = max_attempts or self.max_attempts
        headers = {"Accept": accept} if accept else None
        last_exc: Exception | None = None
        with st.lock:  # una richiesta alla volta per dominio
            for attempt in range(1, attempts + 1):
                self._wait_turn(st)
                started = self._clock()
                try:
                    resp = self._send(url, headers, timeout, post_data)
                except requests.exceptions.SSLError as exc:
                    st.last_request = self._clock()
                    if self._try_aia(url, exc):
                        continue  # ritenta con la catena completata
                    raise FetchError(f"Errore TLS: {exc}") from exc
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                    st.last_request = self._clock()
                    st.requests += 1
                    last_exc = exc
                    log.info("Errore di rete %s (tentativo %d/%d): %s", url, attempt, attempts, exc)
                    if attempt < attempts:
                        self._sleep(self.backoff * (2 ** (attempt - 1)))
                    continue
                st.last_request = self._clock()
                st.requests += 1
                result = FetchResult(url=url, final_url=resp.url, status=resp.status_code, content=resp.content,
                                     headers={k.lower(): v for k, v in resp.headers.items()},
                                     elapsed=self._clock() - started)
                status = resp.status_code
                if status == 429:
                    wait = self._retry_after(result.headers.get("retry-after"))
                    if attempt < attempts and wait is not None and wait <= self.max_retry_after:
                        log.warning("429 da %s: attendo %.0fs (Retry-After)", url, wait)
                        self._sleep(wait)
                        continue
                    st.rate_limited_reason = (f"HTTP 429 persistente su {domain_key(url)} "
                                              f"(Retry-After={result.headers.get('retry-after')}): dominio sospeso per questa esecuzione")
                    raise DomainRateLimited(st.rate_limited_reason)
                if status in RETRY_STATUSES and not self._is_challenge(result):
                    last_exc = FetchError(f"HTTP {status}")
                    if attempt < attempts:
                        self._sleep(self.backoff * (2 ** (attempt - 1)))
                        continue
                    raise FetchError(f"HTTP {status} dopo {attempts} tentativi: {url}")
                if self._is_challenge(result) or status == 403:
                    st.consecutive_403 += 1
                    if self._is_challenge(result) or st.consecutive_403 >= 2:
                        st.blocked_reason = (f"Accesso negato/challenge anti-bot su {domain_key(url)} (HTTP {status}): "
                                             "nessun tentativo di aggiramento")
                        raise DomainBlocked(st.blocked_reason)
                    return result
                st.consecutive_403 = 0
                if result.ok and not result.content.strip():
                    raise FetchError(f"Risposta HTTP {status} con corpo vuoto: {url}")
                return result
        raise FetchError(f"Rete non raggiungibile dopo {attempts} tentativi: {url} ({last_exc})")

    def _send(self, url: str, headers: dict | None, timeout: float | None = None,
              post_data: dict | None = None) -> requests.Response:
        verify = self._verify.get(urlsplit(url).netloc.lower(), True)
        to = (self.timeout[0], timeout) if timeout else self.timeout
        if post_data is not None:
            return self._s.post(url, data=post_data, headers=headers, timeout=to, verify=verify)
        return self._s.get(url, headers=headers, timeout=to, allow_redirects=True, verify=verify)

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if not value:
            return None
        value = value.strip()
        if value.isdigit():
            return float(value)
        try:
            dt = parsedate_to_datetime(value)
            return max(0.0, dt.timestamp() - time.time())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_challenge(res: FetchResult) -> bool:
        if res.headers.get("cf-mitigated", "").lower() == "challenge":
            return True
        if res.status in (403, 429, 503):
            head = res.content[:6000].decode("utf-8", errors="ignore").lower()
            return any(m in head for m in CHALLENGE_MARKERS)
        # Alcuni servizi rispondono 200 con una pagina di verifica JavaScript (es. salute.gov.it):
        # si controllano solo le pagine piccole, per non confondere i normali script anti-bot.
        if res.status == 200 and len(res.content) < 15000:
            head = res.content.decode("utf-8", errors="ignore").lower()
            return any(m in head for m in CHALLENGE_MARKERS_200)
        return False

    # ------------------------------------------------------------------ TLS / AIA
    def _try_aia(self, url: str, exc: Exception) -> bool:
        msg = str(exc)
        host = urlsplit(url).netloc.lower()
        if not self.tls_aia or host in self._verify:
            return False
        if "unable to get local issuer certificate" not in msg and "unable to get issuer certificate" not in msg:
            return False
        try:
            bundle = build_aia_bundle(host.split(":")[0], self.cache_dir, user_agent=self.user_agent)
        except Exception as aia_exc:  # noqa: BLE001 - qualsiasi errore => niente AIA
            log.warning("Completamento AIA fallito per %s: %s", host, aia_exc)
            return False
        if not bundle:
            return False
        log.info("Catena TLS di %s completata via AIA (intermedi verificati): %s", host, bundle)
        self._verify[host] = str(bundle)
        return True


def build_aia_bundle(host: str, cache_dir: Path, *, user_agent: str, port: int = 443) -> Path | None:
    """Scarica gli intermedi mancanti via AIA e crea un bundle certifi+intermedi.

    Ogni certificato scaricato viene accettato solo se la sua firma è verificata dal
    certificato successivo e la catena termina su una radice presente in certifi.
    """
    import certifi
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

    roots = x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())
    roots_by_subject: dict[bytes, list] = {}
    for r in roots:
        roots_by_subject.setdefault(r.subject.public_bytes(), []).append(r)

    # Il certificato foglia viene letto SENZA verifica solo per conoscere l'URL AIA:
    # la connessione vera userà poi il bundle con verifica completa.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    cert = x509.load_der_x509_certificate(der)
    intermediates = []
    for _ in range(4):
        if any(_issued_by(cert, r) for r in roots_by_subject.get(cert.issuer.public_bytes(), [])):
            break  # la catena raggiunge una radice fidata di certifi
        try:
            aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
        except x509.ExtensionNotFound:
            return None
        urls = [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]
        if not urls:
            return None
        raw = requests.get(urls[0], timeout=15, headers={"User-Agent": user_agent}).content
        try:
            issuer = x509.load_der_x509_certificate(raw)
        except ValueError:
            issuer = x509.load_pem_x509_certificate(raw)
        if not _issued_by(cert, issuer):
            raise ValueError("il certificato scaricato via AIA non ha firmato il certificato precedente")
        intermediates.append(issuer)
        cert = issuer
    else:
        return None  # nessuna radice fidata entro 4 livelli: niente bundle
    if not intermediates:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"ca_bundle_{host}.pem"
    pem = Path(certifi.where()).read_text(encoding="ascii")
    pem += "\n" + "".join(c.public_bytes(Encoding.PEM).decode("ascii") for c in intermediates)
    path.write_text(pem, encoding="ascii")
    return path


def _issued_by(child, parent) -> bool:
    try:
        child.verify_directly_issued_by(parent)
        return True
    except Exception:  # noqa: BLE001
        return False
