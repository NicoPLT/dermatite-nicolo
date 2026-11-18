"""Parser robots.txt con supporto ai wildcard (RFC 9309 + estensioni Google).

`urllib.robotparser` della libreria standard non gestisce `*` e `$` nei percorsi,
che invece sono usati da quasi tutti i siti analizzati (PrestaShop, Shopify...).

Semantica:
- si sceglie il gruppo `User-agent` più specifico che contiene il nostro token,
  altrimenti il gruppo `*`; più gruppi con lo stesso agent vengono uniti;
- vince la regola con il percorso più lungo; a parità di lunghezza vince Allow;
- `Disallow:` vuoto non vieta nulla;
- `/robots.txt` è sempre consentito.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass
class RobotsGroup:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)


@dataclass
class RobotsPolicy:
    """Politica per un host. `mode` è 'rules', 'allow_all' o 'disallow_all'."""

    mode: str = "rules"
    groups: list[RobotsGroup] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    crawl_delay: float | None = None
    source_status: int | None = None
    note: str = ""

    @classmethod
    def allow_all(cls, note: str = "", status: int | None = None) -> "RobotsPolicy":
        return cls(mode="allow_all", note=note, source_status=status)

    @classmethod
    def disallow_all(cls, note: str = "", status: int | None = None) -> "RobotsPolicy":
        return cls(mode="disallow_all", note=note, source_status=status)

    def rules_for(self, token: str) -> list[tuple[bool, str]]:
        token = token.lower()
        specific = [g for g in self.groups if any(a != "*" and a in token for a in g.agents)]
        chosen = specific or [g for g in self.groups if "*" in g.agents]
        return [r for g in chosen for r in g.rules]

    def is_allowed(self, url: str, token: str) -> bool:
        if self.mode == "allow_all":
            return True
        if self.mode == "disallow_all":
            return False
        parts = urlsplit(url)
        path = parts.path or "/"
        if path == "/robots.txt":
            return True
        target = unquote(path) + (("?" + unquote(parts.query)) if parts.query else "")
        best: tuple[bool, int] | None = None
        for allow, pattern in self.rules_for(token):
            if not pattern:
                continue  # "Disallow:" vuoto = nessun divieto
            if _compile(pattern).match(target):
                length = len(pattern)
                if best is None or length > best[1] or (length == best[1] and allow and not best[0]):
                    best = (allow, length)
        return True if best is None else best[0]


_CACHE: dict[str, re.Pattern] = {}


def _compile(pattern: str) -> re.Pattern:
    rx = _CACHE.get(pattern)
    if rx is None:
        anchored = pattern.endswith("$")
        body = unquote(pattern[:-1] if anchored else pattern)
        rx = re.compile("^" + ".*".join(re.escape(part) for part in body.split("*")) + ("$" if anchored else ""))
        _CACHE[pattern] = rx
    return rx


def parse_robots(text: str, status: int | None = 200) -> RobotsPolicy:
    groups: list[RobotsGroup] = []
    sitemaps: list[str] = []
    crawl_delay: float | None = None
    current: RobotsGroup | None = None
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = RobotsGroup()
                groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if key in ("allow", "disallow") and current is not None:
            current.rules.append((key == "allow", value))
        elif key == "sitemap" and value:
            sitemaps.append(value)
        elif key == "crawl-delay" and current is not None and "*" in current.agents:
            try:
                crawl_delay = float(value)
            except ValueError:
                pass
    return RobotsPolicy(mode="rules", groups=groups, sitemaps=sitemaps, crawl_delay=crawl_delay, source_status=status)


def policy_from_response(status: int | None, text: str | None) -> RobotsPolicy:
    """Applica RFC 9309 sezione 2.3.1 al risultato del download di robots.txt."""
    if status is None:
        return RobotsPolicy.disallow_all("robots.txt irraggiungibile: per prudenza nessun accesso", status)
    if 200 <= status < 300:
        return parse_robots(text or "", status)
    if status == 429 or status >= 500:
        return RobotsPolicy.disallow_all(f"robots.txt HTTP {status}: accesso sospeso", status)
    if 400 <= status < 500:
        return RobotsPolicy.allow_all(f"robots.txt HTTP {status}: nessuna regola (RFC 9309)", status)
    return RobotsPolicy.disallow_all(f"robots.txt HTTP {status} inatteso", status)
