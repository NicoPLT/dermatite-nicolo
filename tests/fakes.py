"""Finto server HTTP per i test (nessuna rete). Usato da più file di test."""
from scrapers.http import PoliteSession


class FakeResp:
    def __init__(self, status=200, body=b"<html>ok</html>", headers=None, url=None):
        self.status_code, self.content, self.headers, self.url = status, body, headers or {}, url


class FakeRequests:
    """Instrada le GET verso risposte predefinite e registra ogni chiamata."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def get(self, url, headers=None, timeout=None, allow_redirects=True, verify=True, **_):
        self.calls.append(url)
        r = self.routes.get(url)
        if callable(r):
            r = r()
        if isinstance(r, list):
            r = r.pop(0)
        r = r or FakeResp(404, b"not found")
        r.url = r.url or url
        return r


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def now(self):
        return self.t


def public_resolver(host):
    """Nessuna query DNS nei test: ogni host 'risolve' a un IP pubblico, salvo i nomi in PRIVATE_HOSTS."""
    return PRIVATE_HOSTS.get(host, ["93.184.216.34"])


PRIVATE_HOSTS = {"intranet.farmacia-test.it": ["10.0.0.5"]}


def make_session(routes, *, resolver=public_resolver, **cfg):
    clock = Clock()
    http_cfg = {"user_agent": "TestUA/1.0", "robots_token": "TestUA", "min_delay_seconds": 2.5, "jitter_seconds": 0,
                "max_attempts": 3, "backoff_base_seconds": 1, "max_retry_after_seconds": 90, **cfg}
    fake = FakeRequests(routes)
    s = PoliteSession(http_cfg, sleep=clock.sleep, clock=clock.now, session=fake, resolver=resolver)
    return s, fake, clock
