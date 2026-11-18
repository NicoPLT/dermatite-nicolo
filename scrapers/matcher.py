"""Identificazione rigorosa prodotto/formato.

Ordine delle decisioni:
1. identificativi (EAN/GTIN e Minsan) letti dalla pagina:
   - coincidono con quelli del catalogo  -> match "ID" (affidabilità alta)
   - presenti ma diversi                 -> escluso ("ID_DIVERSO")
2. senza identificativi: nome + marca + formato con regole rigorose -> "NOME_FORMATO" (affidabilità media)
3. in ogni caso: multipack/bundle, varianti escluse o formato diverso => escluso.

Il formato si legge solo dai titoli (JSON-LD name, h1, og:title), mai dalla descrizione,
che spesso cita altri formati o prodotti.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .extract import PageData

MATCH_ID = "ID"
MATCH_NAME = "NOME_FORMATO"
REJECT_ID_DIFFERENT = "ID_DIVERSO"
REJECT_ID_CONTRADICTION = "ID_CONTRADDITTORIO"
REJECT_VARIANT = "VARIANTE_ESCLUSA"
REJECT_BUNDLE = "MULTIPACK_O_BUNDLE"
REJECT_FORMAT = "FORMATO_DIVERSO"
REJECT_FORMAT_UNKNOWN = "FORMATO_NON_VERIFICABILE"
REJECT_NAME = "NOME_NON_CORRISPONDE"
REJECT_BRAND = "MARCA_DIVERSA"
ACCEPTED_LEVELS = {MATCH_ID, MATCH_NAME}

# Alias applicati al testo normalizzato (prima della rimozione di + e -).
_ALIASES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bap\s*[+\-]?\s*(?:piu\s*|plus\s*)?max\b"), " apmax "),
    (re.compile(r"\bap\s*[+\-]?\s*(?:piu\s*|plus\s*)?m\b"), " apm "),
    (re.compile(r"\buv\s*-?\s*mune\b|\buvm\b|\buvm(?=\d)"), " uvmune "),
    (re.compile(r"\binv\b|\binvisible\b|\binvisibili\b"), " invisibile "),
    (re.compile(r"\bfluide\b|\bfluid\b"), " fluido "),
    (re.compile(r"\blipo\s*-?\s*crema\b"), " lipocrema "),
    (re.compile(r"\bcrema\s*-?\s*base\b"), " cremabase "),
    (re.compile(r"\bsenza\s+profum\w*|\bsans\s+parfum\b|\bs\s*\.?\s*prof\b|\bfragrance\s*-?\s*free\b|\bnon\s+profumat\w*"), " senzaprofumo "),
    (re.compile(r"\bcon\s+profum\w*|\bprofumat[oaie]\b|\bparfume?e?\b"), " conprofumo "),
    (re.compile(r"\boil\s*-?\s*control\b"), " oil control "),
    (re.compile(r"\bdermo\s*-?\s*pediatrics?\b"), " pediatrics "),
    (re.compile(r"\bteintee?\b|\bteinte\b"), " teinte "),
    (re.compile(r"\bla\s*-?\s*roche\s*-?\s*posay\b|\blrp\b"), " la roche posay "),
]

_BUNDLE_RX = [
    re.compile(r"\bset\s+da\s+\d"), re.compile(r"\bduo\s*-?\s*pack\b"), re.compile(r"\bduopack\b"),
    re.compile(r"\b(?:bi|tri)pack\b"), re.compile(r"\b\d\s*x\s*\d"), re.compile(r"\bx\s*[2-9]\b"),
    re.compile(r"\b[2-9]\s*x\b"), re.compile(r"\b[2-9]\s+pezzi\b"), re.compile(r"\bconfezione\s+doppia\b"),
    re.compile(r"\bkit\b"), re.compile(r"\bcofanetto\b"), re.compile(r"\bomaggio\b"), re.compile(r"\bcampion\w*\b"),
    re.compile(r"\bsample\b"), re.compile(r"\btravel\s+size\b"), re.compile(r"\brefill\b"), re.compile(r"\bricarica\b"),
    re.compile(r"\bduo\b"),
]

_VOLUME_RX = re.compile(r"(?<![\w.,])(\d{1,4}(?:[.,]\d{1,3})?)\s*(ml|millilitri|lt|litri|litro|l)\b", re.I)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str) -> str:
    s = strip_accents(text or "").lower()
    s = s.replace("&", " e ").replace("’", "'")
    s = re.sub(r"[\"'`´“”«»()\[\]{}|,;:!?/\\_*]", " ", s)
    for rx, repl in _ALIASES:
        s = rx.sub(repl, s)
    s = re.sub(r"(?<=\d)\.(?=\d)", "", s)  # 1.000 -> 1000
    s = re.sub(r"[+\-.]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(text: str) -> set[str]:
    return set(normalize(text).split())


def has_term(norm_text: str, term: str) -> bool:
    t = normalize(term)
    return bool(t) and f" {t} " in f" {norm_text} "


def volumes_ml(text: str) -> set[int]:
    """Volumi espliciti in ml ('1 L' -> 1000, '50ml' -> 50). Ignora 'SPF50', '311'."""
    s = strip_accents(text or "").replace("-", " ").replace("_", " ")
    out = set()
    for num, unit in _VOLUME_RX.findall(s):
        value = float(num.replace(",", "."))
        ml = value * 1000 if unit.lower() in ("l", "lt", "litro", "litri") else value
        if 0 < ml <= 5000:
            out.add(int(round(ml)))
    return out


@dataclass
class MatchResult:
    level: str
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    title: str = ""
    volumes: list[int] = field(default_factory=list)
    id_matched: list[str] = field(default_factory=list)

    @property
    def reliability(self) -> str:
        return {MATCH_ID: "alta", MATCH_NAME: "media"}.get(self.level, "esclusa")


def _bundle_reasons(norm_title: str, vols: set[int]) -> list[str]:
    reasons = [f"indicatore multipack '{rx.pattern}'" for rx in _BUNDLE_RX if rx.search(norm_title)]
    if len(vols) > 1:
        reasons.append(f"più formati nel titolo {sorted(vols)} (bundle)")
    return reasons


def match_product(product: dict, page: PageData, global_bundle_terms: list[str] | None = None) -> MatchResult:
    rules = product.get("match", {})
    fmt = int(product["format"]["quantity"])
    titles = [t for k, t in page.titles.items() if k in ("json_ld", "h1", "og", "microdata") and t]
    title = page.title
    norm_title = normalize(" ".join(dict.fromkeys(titles)) or title)
    vols: set[int] = set()
    for t in titles or [title]:
        vols |= volumes_ml(t)

    res = MatchResult(level=REJECT_NAME, accepted=False, title=title, volumes=sorted(vols))

    # --- identificativi
    cfg_ean, cfg_minsan = product.get("ean"), product.get("minsan")
    ean_match = bool(cfg_ean and cfg_ean in page.eans)
    minsan_match = bool(cfg_minsan and cfg_minsan in page.minsans)
    ean_conflict = bool(cfg_ean and page.eans and cfg_ean not in page.eans)
    minsan_conflict = bool(cfg_minsan and page.minsans and cfg_minsan not in page.minsans)
    if ean_match:
        res.id_matched.append(f"EAN {cfg_ean}")
    if minsan_match:
        res.id_matched.append(f"Minsan {cfg_minsan}")

    # --- varianti, bundle e formato (valgono sempre, anche con ID)
    problems: list[tuple[str, str]] = []
    bundle_terms = [normalize(t) for t in (global_bundle_terms or [])]
    for reason in _bundle_reasons(norm_title, vols):
        problems.append((REJECT_BUNDLE, reason))
    for term in bundle_terms:
        if term and f" {term} " in f" {norm_title} ":
            problems.append((REJECT_BUNDLE, f"termine multipack '{term}'"))
    for term in rules.get("excluded_terms", []):
        if has_term(norm_title, term):
            problems.append((REJECT_VARIANT, f"variante esclusa '{term}'"))
    if vols and vols != {fmt}:
        problems.append((REJECT_FORMAT, f"formato nel titolo {sorted(vols)} ml diverso da {fmt} ml"))

    if ean_match or minsan_match:
        if ean_conflict or minsan_conflict:
            res.level = REJECT_ID_CONTRADICTION
            res.reasons.append(f"identificativi contraddittori: EAN pagina {sorted(page.eans)}, Minsan pagina {sorted(page.minsans)}")
            return res
        if problems:
            res.level = problems[0][0]
            res.reasons += [f"{' e '.join(res.id_matched)} coincide ma: {p[1]}" for p in problems]
            return res
        res.level, res.accepted = MATCH_ID, True
        res.reasons.append("identificativo coincidente: " + ", ".join(res.id_matched))
        return res

    if ean_conflict or minsan_conflict:
        res.level = REJECT_ID_DIFFERENT
        res.reasons.append(f"identificativi della pagina diversi dal catalogo (EAN {sorted(page.eans)}, Minsan {sorted(page.minsans)})")
        return res

    # --- nessun identificativo: nome + marca + formato (regole più severe)
    for term in rules.get("excluded_terms_name_only", []):
        if has_term(norm_title, term):
            problems.append((REJECT_VARIANT, f"termine '{term}' non ammesso senza identificativo"))
    if problems:
        res.level = problems[0][0]
        res.reasons += [p[1] for p in problems]
        return res
    toks = set(norm_title.split())
    missing = [t for t in rules.get("required_all", []) if normalize(t) not in toks]
    if missing:
        res.reasons.append(f"mancano i termini obbligatori {missing}")
        return res
    for group in rules.get("required_any", []):
        if not any(has_term(norm_title, t) for t in group):
            res.reasons.append(f"manca uno dei termini {group}")
            return res
    if page.brand:
        nb = normalize(page.brand)
        accepted = [normalize(a) for a in rules.get("brand_aliases", [])] + [normalize(t) for t in rules.get("required_all", [])]
        if not any(a and (a in nb or nb in a) for a in accepted):
            res.level = REJECT_BRAND
            res.reasons.append(f"marca della pagina '{page.brand}' non compatibile")
            return res
    if not vols:
        res.level = REJECT_FORMAT_UNKNOWN
        res.reasons.append("formato non indicato nel titolo e nessun identificativo: impossibile verificare")
        return res
    res.level, res.accepted = MATCH_NAME, True
    res.reasons.append(f"nome, marca e formato {fmt} ml coincidenti (nessun identificativo in pagina)")
    return res
