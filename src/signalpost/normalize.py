"""Norwegian-specific normalisation helpers.

Everything here is deterministic and pure so that identity decisions can be
replayed from a stored snapshot without network access.
"""
from __future__ import annotations

import re
import unicodedata

# Legal-form suffixes that carry no discriminating power in a company name.
LEGAL_SUFFIXES: frozenset[str] = frozenset({
    "as", "a/s", "asa", "ans", "da", "nuf", "ba", "sa", "enk", "brl", "bbl",
    "sti", "ks", "is", "fli", "esek", "spa", "sam", "iks", "kf", "sf", "pre",
    "pk", "vpfo", "gfs", "kirk", "ltd", "limited", "ab", "oy", "gmbh", "inc",
})

# Words that make a company name look like a plain street address. Norwegian
# property companies are routinely named after their own address, which makes a
# bare name match on another company's contact page actively dangerous.
STREET_WORDS: frozenset[str] = frozenset({
    "vei", "veien", "veg", "vegen", "vn", "gate", "gaten", "gata", "gt",
    "plass", "plassen", "allé", "alle", "alleen", "brygge", "bryggen", "brygga",
    "torg", "torget", "bakken", "haugen", "lia", "stien", "svingen", "kaia",
    "kaien", "ringen", "parken", "jordet", "tunet", "kroken", "faret", "hagen",
})

# Suffixes a single compound token can end in, e.g. "stangnesvei", "storgata".
STREET_SUFFIXES: tuple[str, ...] = (
    "veien", "vegen", "vei", "veg", "gata", "gaten", "gate", "plassen", "plass",
    "bakken", "haugen", "stien", "svingen", "alleen", "torget", "brygga",
    "bryggen", "kaien", "ringen", "tunet", "jordet",
)

FREEMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "hotmail.co.uk",
    "outlook.com", "outlook.no", "yahoo.com", "yahoo.no", "yahoo.se", "live.no",
    "live.com", "online.no", "icloud.com", "me.com", "mac.com", "msn.com",
    "broadpark.no", "start.no", "c2i.net", "frisurf.no", "getmail.no",
    "lyse.net", "altibox.no", "proton.me", "protonmail.com", "aol.com",
    "mail.com", "homenet.no", "sf-nett.no", "epost.no", "telia.com", "hush.com",
})

# Hosts that are platforms rather than a company's own site.
PLATFORM_HOSTS: frozenset[str] = frozenset({
    "facebook.com", "linkedin.com", "instagram.com", "x.com", "twitter.com",
    "youtube.com", "tiktok.com", "finn.no", "proff.no", "1881.no", "gulesider.no",
    "purehelp.no", "brreg.no", "google.com", "wordpress.com", "wixsite.com",
    "blogspot.com", "sites.google.com", "notion.site", "github.io", "medium.com",
})

_WS = re.compile(r"\s+")
_NONWORD = re.compile(r"[^\w\s]", re.UNICODE)
_TAGS = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_ANYTAG = re.compile(r"<[^>]+>")

# 9 digits, optionally grouped 3-3-3 by space, non-breaking space, dot or hyphen.
# The lookarounds must reject a nine-digit window *inside* a longer grouped
# number: a share capital printed as "kr 5 923 609 016" would otherwise yield
# "923609016", which is mod-11 valid and would veto a correct candidate as a
# conflicting organisation number.
_SEP = "[\\s.\u00a0-]"
ORG_NUMBER_RE = re.compile(
    "(?<!\\d)"                      # not continuing a digit run
    "(?<!\\d" + _SEP + ")"          # not continuing a grouped digit run
    "(\\d{3})" + _SEP + "?(\\d{3})" + _SEP + "?(\\d{3})"
    "(?!" + _SEP + "?\\d)"          # not followed by a further group
)


def strip_accents(value: str) -> str:
    """Fold Norwegian letters the way domain names conventionally do."""
    table = {"æ": "ae", "Æ": "ae", "ø": "o", "Ø": "o", "å": "a", "Å": "a"}
    out = "".join(table.get(ch, ch) for ch in value)
    return "".join(c for c in unicodedata.normalize("NFD", out) if not unicodedata.combining(c))


def normalise_text(html_or_text: str) -> str:
    """Lower-cased, tag-free, whitespace-collapsed text for substring matching."""
    txt = _TAGS.sub(" ", html_or_text)
    txt = _ANYTAG.sub(" ", txt)
    txt = unicodedata.normalize("NFKC", txt)
    txt = txt.replace(" ", " ").lower()
    return _WS.sub(" ", txt).strip()


def name_tokens(name: str) -> list[str]:
    """Discriminating tokens of a legal name, legal form and branch markers removed."""
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = s.replace("/", " ")
    s = _NONWORD.sub(" ", s)
    parts = [p for p in s.split() if p]
    if "avd" in parts:                      # "... AVD OSLO" is a branch suffix
        parts = parts[: parts.index("avd")]
    while parts and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()
    while parts and parts[0] in LEGAL_SUFFIXES:
        parts.pop(0)
    return parts


def normalise_name(name: str) -> str:
    return " ".join(name_tokens(name))


def looks_like_address(name: str) -> bool:
    """True when a legal name is really a street address, e.g. 'SKJEBERGVEIEN 25 AS'.

    Such a name appearing on an unrelated page is almost always that page quoting
    an address, not identifying itself, so it must not count as a name match.
    """
    toks = name_tokens(name)
    if not toks:
        return False
    has_number = any(re.fullmatch(r"\d{1,4}[a-z]?", t) for t in toks)
    has_street = any(
        t in STREET_WORDS or (len(t) > 5 and t.endswith(STREET_SUFFIXES))
        for t in toks
    )
    return has_number and (has_street or len(toks) <= 2)


def normalise_phone(value: str | None) -> str:
    """Last 8 digits - the Norwegian subscriber number, country code agnostic."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-8:] if len(digits) >= 8 else ""


def mod11_valid(org_number: str) -> bool:
    """Brreg organisation numbers carry a mod-11 check digit."""
    if len(org_number) != 9 or not org_number.isdigit():
        return False
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    remainder = sum(int(c) * w for c, w in zip(org_number[:8], weights)) % 11
    check = 0 if remainder == 0 else 11 - remainder
    return check < 10 and check == int(org_number[8])


def find_org_numbers(text: str) -> set[str]:
    """Every mod-11-valid organisation number appearing in a page."""
    return {
        joined
        for m in ORG_NUMBER_RE.finditer(text)
        if mod11_valid(joined := "".join(m.groups()))
    }


def registered_domain(url_or_host: str) -> str:
    """Registrable domain without a public-suffix list dependency at import time."""
    host = (url_or_host or "").strip().lower()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)
    host = host.split("/")[0].split("?")[0].split("#")[0].split("@")[-1]
    host = host.split(":")[0].rstrip(".")
    if not host or "." not in host:
        return ""
    labels = host.split(".")
    # Norwegian second-level suffixes that behave like a public suffix.
    two_level = {"co.uk", "com.au", "co.nz", "priv.no", "fhs.no", "vgs.no", "herad.no", "kommune.no"}
    if len(labels) >= 3 and ".".join(labels[-2:]) in two_level:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_platform_host(url_or_host: str) -> bool:
    return registered_domain(url_or_host) in PLATFORM_HOSTS


def email_domain(address: str | None) -> str:
    """Registrable domain of a corporate e-mail address, '' for freemail."""
    value = (address or "").strip().lower().strip(" .<>")
    if "@" not in value:
        return ""
    domain = registered_domain(value.rsplit("@", 1)[-1])
    if not domain or domain in FREEMAIL_DOMAINS:
        return ""
    return domain


def domain_candidates_from_name(name: str, limit: int = 6) -> list[str]:
    """Deterministic domain guesses from a legal name.

    Measured at a 4.7% verified hit rate on its own (experiments/exp01), so this is
    a candidate generator of last resort, never a source of truth.
    """
    toks = name_tokens(name)
    if not toks:
        return []
    out: list[str] = []
    folded = [re.sub(r"[^a-z0-9]", "", strip_accents(t)) for t in toks]
    folded = [t for t in folded if t]
    if not folded:
        return []
    stems = ["".join(folded), "-".join(folded)]
    if len(folded[0]) >= 5:
        stems.append(folded[0])
    if len(folded) >= 2:
        stems.append("".join(folded[:2]))
    for stem in stems:
        if not 3 <= len(stem) <= 63:
            continue
        for tld in (".no", ".com"):
            candidate = stem + tld
            if candidate not in out:
                out.append(candidate)
    return out[:limit]
