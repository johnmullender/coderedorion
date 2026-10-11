"""Check data/resources.json against the Phase 2 contract.

Usage:
    python3 scripts/validate.py [path] [--check-sources]

Errors make the exit code 1. Warnings (needs_review true, no hours at all, a source
page that cannot be downloaded) do not. --check-sources needs internet: it downloads
each distinct source_url once and looks for every source_quote and hours_quote.
"""

import argparse
import html
import json
import math
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO_ROOT / "data" / "resources.json"

NEEDS = ("food", "shelter", "hygiene", "clinic", "library", "cooling", "legal")
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RULE_TYPES = ("id_required", "age", "serves", "zip_in", "residency", "income_limit", "note")
SERVE_GROUPS = ("women", "men", "families", "youth", "veterans", "seniors")
# Must match OFFER_KEYS in brain.py (tests check that the two lists are the same).
OFFER_KEYS = (
    "food_pantry", "hot_meals", "shelter_beds", "showers", "laundry", "restrooms", "clothing",
    "wifi", "computers", "phone_charging", "mail", "id_help", "medical_care", "dental_care",
    "pharmacy", "mental_health", "legal_aid", "immigration_help", "case_management", "job_help",
    "cooling", "heating", "hygiene_items", "baby_supplies",
)
LAT_BOUNDS = (29.4, 30.2)
LNG_BOUNDS = (-95.9, -94.9)

SOURCE_TIMEOUT_SECONDS = 15.0
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
MAX_QUOTE_SHOWN = 80

_RANGE_RE = re.compile(r"([0-9]{2}):([0-9]{2})-([0-9]{2}):([0-9]{2})")
_ZIP_RE = re.compile(r"[0-9]{5}")
_SKIP_BLOCK_RE = re.compile(r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_TAG_NAME_RE = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)")
# Tags that sit inside a word or sentence: removing them keeps the words joined ("Fr<b>ee</b>").
_INLINE_TAGS = frozenset({"a", "b", "strong", "i", "em", "span", "u", "small", "font", "abbr",
                          "sup", "sub", "mark", "code"})
_SPACE_RE = re.compile(r"\s+")
_DASH_SPACE_RE = re.compile(r"\s*-\s*")

# Curly quotes, dashes and a few look-alikes -> plain ASCII. Applied before lower-casing.
_CHAR_TABLE = {ord(k): v for k, v in {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    "…": "...", "​": "", "﻿": "",
}.items()}


# ---------------------------------------------------------------- text helpers

def normalize_text(text):
    """Lower case, plain quotes and dashes, spaces around dashes removed, whitespace collapsed."""
    if not text:
        return ""
    out = str(text).translate(_CHAR_TABLE).lower()
    out = _DASH_SPACE_RE.sub("-", out)
    return _SPACE_RE.sub(" ", out).strip()


def _tag_replacement(match):
    """Inline tags vanish (words stay joined); every other tag becomes a space."""
    name = _TAG_NAME_RE.match(match.group(0))
    if name and name.group(1).lower() in _INLINE_TAGS:
        return ""
    return " "


def html_to_text(html_text):
    """Visible text of an HTML page: no scripts, styles or comments, no tags, entities decoded."""
    text = _COMMENT_RE.sub(" ", html_text or "")
    text = _SKIP_BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(_tag_replacement, text)
    text = html.unescape(text)
    return _SPACE_RE.sub(" ", text).strip()


def quote_in_page(quote, page_text):
    """True if the quote appears in the page text, ignoring case, spacing, quotes and dashes."""
    needle = normalize_text(quote)
    if not needle:
        return False
    page = normalize_text(page_text)
    if needle in page:
        return True
    # Last resort: spacing may differ inside the sentence (e.g. a tag or line break).
    return _SPACE_RE.sub("", needle) in _SPACE_RE.sub("", page)


def download_page(url):
    """Fetch a source page as text. Raises on network or HTTP errors."""
    response = httpx.get(
        url,
        follow_redirects=True,
        timeout=SOURCE_TIMEOUT_SECONDS,
        headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
    )
    response.raise_for_status()
    return response.text


# ---------------------------------------------------------------- schema checks

def _nonempty(value):
    """True for a string with some non-space text."""
    return isinstance(value, str) and value.strip() != ""


def _is_number(value):
    """True for a finite int or float (bool does not count)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_whole(value):
    """True for an int (bool does not count)."""
    return isinstance(value, int) and not isinstance(value, bool)


def hours_range_error(text):
    """Return a problem with one "HH:MM-HH:MM" range, or None if it is valid.

    End may be 24:00. A range may cross midnight (18:00-07:00). Start and end may not be equal.
    """
    if not isinstance(text, str):
        return f"range {text!r} is not a string"
    match = _RANGE_RE.fullmatch(text)
    if not match:
        return f'"{text}" is not in HH:MM-HH:MM form'
    sh, sm, eh, em = (int(g) for g in match.groups())
    if sh > 23 or sm > 59:
        return f'"{text}" has an invalid start time'
    if not (eh == 24 and em == 0) and (eh > 23 or em > 59):
        return f'"{text}" has an invalid end time'
    if (sh, sm) == (eh, em):
        return f'"{text}" starts and ends at the same time'
    return None


def _check_category(value, errors):
    if not isinstance(value, list) or not value:
        errors.append("category must be a non-empty list")
        return
    unknown = [c for c in value if c not in NEEDS]
    if unknown:
        errors.append(f"category has unknown need(s) {unknown}; allowed: {', '.join(NEEDS)}")


def _check_coords(place, errors):
    for key, (low, high) in (("lat", LAT_BOUNDS), ("lng", LNG_BOUNDS)):
        value = place.get(key)
        if value is None:
            errors.append(f"{key} is missing")
        elif not _is_number(value):
            errors.append(f"{key} is not a number: {value!r}")
        elif not low <= value <= high:
            errors.append(f"{key} {value} is outside the Houston bounds {low} to {high}")


def _check_hours(hours, errors, warnings):
    if not isinstance(hours, dict):
        errors.append("hours must be an object with keys mon, tue, wed, thu, fri, sat, sun")
        return
    missing = [d for d in DAY_KEYS if d not in hours]
    unknown = [k for k in hours if k not in DAY_KEYS]
    if missing:
        errors.append(f"hours is missing day key(s): {', '.join(missing)}")
    if unknown:
        errors.append(f"hours has unknown key(s): {', '.join(str(k) for k in unknown)}")
    total_ranges = 0
    for day in DAY_KEYS:
        if day not in hours:
            continue
        ranges = hours[day]
        if not isinstance(ranges, list):
            errors.append(f"hours.{day} must be a list of ranges")
            continue
        total_ranges += len(ranges)
        for text in ranges:
            problem = hours_range_error(text)
            if problem:
                errors.append(f"hours.{day}: {problem}")
    if total_ranges == 0:
        warnings.append("hours are empty: this place is never shown as open")


def _check_source_url(url, errors):
    if not _nonempty(url):
        errors.append("source_url is missing or empty")
    elif not _valid_http_url(url):
        errors.append(f"source_url must be an http(s) URL: {url}")


def _valid_http_url(url):
    """True for a non-empty http or https URL with a host."""
    if not _nonempty(url):
        return False
    parts = urlparse(url.strip())
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _rule_age(rule):
    problems = []
    low, high = rule.get("min"), rule.get("max")
    for name, value in (("min", low), ("max", high)):
        if value is not None and (not _is_whole(value) or value < 0):
            problems.append(f"{name} must be a whole number or null")
    if low is None and high is None:
        problems.append("min and max are both null")
    elif not problems and low is not None and high is not None and low > high:
        problems.append("min is greater than max")
    return problems


def _rule_serves(rule):
    if rule.get("group") not in SERVE_GROUPS:
        return [f"group must be one of {', '.join(SERVE_GROUPS)}"]
    return []


def _rule_zip_in(rule):
    zips = rule.get("zips")
    if not isinstance(zips, list) or not zips:
        return ["zips must be a non-empty list"]
    bad = [z for z in zips if not (isinstance(z, str) and _ZIP_RE.fullmatch(z))]
    if bad:
        return [f"zips must be 5-digit strings, got {bad}"]
    return []


def _rule_residency(rule):
    return [] if _nonempty(rule.get("area")) else ["area is missing or empty"]


def _rule_income(rule):
    return [] if _nonempty(rule.get("question")) else ["question is missing or empty"]


def _rule_note(rule):
    return [] if _nonempty(rule.get("text")) else ["text is missing or empty"]


_RULE_CHECKS = {
    "id_required": lambda rule: [],
    "age": _rule_age,
    "serves": _rule_serves,
    "zip_in": _rule_zip_in,
    "residency": _rule_residency,
    "income_limit": _rule_income,
    "note": _rule_note,
}


def _check_eligibility(rules, errors):
    if not isinstance(rules, list):
        errors.append("eligibility must be a list (use [] when the source states no rules)")
        return
    for i, rule in enumerate(rules):
        where = f"eligibility[{i}]"
        if not isinstance(rule, dict):
            errors.append(f"{where} is not an object")
            continue
        rtype = rule.get("type")
        if not (isinstance(rtype, str) and rtype in _RULE_CHECKS):
            errors.append(f"{where} has unknown type {rtype!r}; allowed: {', '.join(RULE_TYPES)}")
            rtype_label = "?"
        else:
            rtype_label = rtype
        if not _nonempty(rule.get("source_quote")):
            errors.append(f"{where} ({rtype_label}) has no source_quote")
        if rtype_label != "?":
            for problem in _RULE_CHECKS[rtype_label](rule):
                errors.append(f"{where} ({rtype_label}): {problem}")


def _check_offers(offers, errors):
    """Optional offers: a list of {"key", "quote", "url"}. The key comes from OFFER_KEYS, the quote is
    copied from the page at url, and url is an http(s) link."""
    if offers is None:
        return
    if not isinstance(offers, list):
        errors.append("offers must be a list (use [] when the place lists none)")
        return
    for i, offer in enumerate(offers):
        where = f"offers[{i}]"
        if not isinstance(offer, dict):
            errors.append(f"{where} is not an object")
            continue
        key = offer.get("key")
        if not (isinstance(key, str) and key in OFFER_KEYS):
            errors.append(f"{where} has unknown key {key!r}; allowed: {', '.join(OFFER_KEYS)}")
        if not _nonempty(offer.get("quote")):
            errors.append(f"{where} ({key}) has no quote")
        if not _valid_http_url(offer.get("url")):
            errors.append(f"{where} ({key}) url must be an http(s) URL")


def validate_place(place):
    """Check one place record. Returns (errors, warnings), two lists of strings."""
    if not isinstance(place, dict):
        return ["record is not a JSON object"], []
    errors, warnings = [], []
    for key in ("id", "name", "address"):
        if not _nonempty(place.get(key)):
            errors.append(f"{key} is missing or empty")
    _check_category(place.get("category"), errors)
    _check_coords(place, errors)
    _check_hours(place.get("hours"), errors, warnings)
    _check_source_url(place.get("source_url"), errors)
    _check_eligibility(place.get("eligibility"), errors)
    _check_offers(place.get("offers"), errors)
    if place.get("needs_review") is True:
        warnings.append("needs_review is true: no human has checked this place yet")
    return errors, warnings


def place_label(place, index):
    """'#3 some-id - Name' for report lines."""
    if isinstance(place, dict):
        return f"#{index + 1} {place.get('id', '?')} - {place.get('name', '?')}"
    return f"#{index + 1} (not an object)"


def validate_resources(places):
    """Check every place and the uniqueness of ids.

    Returns a list of [label, errors, warnings], one per place, in list order.
    """
    results = []
    first_label = {}
    for index, place in enumerate(places):
        errors, warnings = validate_place(place)
        label = place_label(place, index)
        pid = place.get("id") if isinstance(place, dict) else None
        if _nonempty(pid):
            if pid in first_label:
                errors.append(f'duplicate id "{pid}" (also used by {first_label[pid]})')
            else:
                first_label[pid] = label
        results.append([label, errors, warnings])
    return results


# ---------------------------------------------------------------- source checks

def _short(text):
    """A quote shortened for a report line."""
    text = _SPACE_RE.sub(" ", str(text)).strip()
    return text if len(text) <= MAX_QUOTE_SHOWN else text[: MAX_QUOTE_SHOWN - 3] + "..."


def _quotes_for(place):
    """(kind, quote, url) triples to look for on source pages.

    Rules and hours are looked for on the place's source_url; each offer is looked for on its own url.
    """
    source = place.get("source_url")
    quotes = []
    for rule in place.get("eligibility") or []:
        if isinstance(rule, dict) and _nonempty(rule.get("source_quote")):
            quotes.append(("source_quote", rule["source_quote"], source))
    if _nonempty(place.get("hours_quote")):
        quotes.append(("hours_quote", place["hours_quote"], source))
    for offer in place.get("offers") or []:
        if isinstance(offer, dict) and _nonempty(offer.get("quote")):
            quotes.append(("offer quote", offer["quote"], offer.get("url")))
    return quotes


def _page_for(url, pages, fetch):
    """(page text or None, problem or None) for a url, downloaded once."""
    if url not in pages:
        try:
            pages[url] = (html_to_text(fetch(url)), None)
        except Exception as exc:  # any failure is a warning, not a crash
            pages[url] = (None, str(exc) or type(exc).__name__)
    return pages[url]


def check_sources(places, downloader=None):
    """Look for every quote on its source page.

    Each distinct url is downloaded once. Returns {index: (errors, warnings)} for the places that
    have a valid source_url or an offer with a valid url.
    """
    fetch = downloader or download_page
    pages = {}  # url -> (page text or None, problem or None)
    results = {}
    for index, place in enumerate(places):
        if not isinstance(place, dict):
            continue
        source = place.get("source_url")
        has_source = _valid_http_url(source)
        checks = [(kind, quote, url.strip()) for kind, quote, url in _quotes_for(place)
                  if _valid_http_url(url)]
        if not has_source and not checks:
            continue
        errors, warnings = [], []
        if has_source:
            _text, problem = _page_for(source.strip(), pages, fetch)
            if problem is not None:
                warnings.append(f"could not download source_url {source.strip()} ({problem}); quotes not checked")
        for kind, quote, url in checks:
            text, problem = _page_for(url, pages, fetch)
            if problem is not None:
                warning = f"could not download source_url {url} ({problem}); quotes not checked"
                if warning not in warnings:
                    warnings.append(warning)
            elif not quote_in_page(quote, text):
                errors.append(f'{kind} not found on {url}: "{_short(quote)}"')
        results[index] = (errors, warnings)
    return results


def build_results(places, check_src=False, downloader=None):
    """validate_resources plus, if asked, the source checks. Same shape as validate_resources."""
    results = validate_resources(places)
    if check_src:
        for index, (errors, warnings) in check_sources(places, downloader).items():
            results[index][1].extend(errors)
            results[index][2].extend(warnings)
    return results


def format_report(results, path, check_src):
    """Plain-text report grouped by place, with a summary line."""
    lines = [f"Checking {len(results)} place(s) in {path}"
             + (" (source pages checked)" if check_src else "")]
    total_errors = total_warnings = 0
    for label, errors, warnings in results:
        status = "FAIL" if errors else ("WARN" if warnings else "OK")
        lines.append(f"{status:<5} {label}")
        for message in errors:
            lines.append(f"        error:   {message}")
        for message in warnings:
            lines.append(f"        warning: {message}")
        total_errors += len(errors)
        total_warnings += len(warnings)
    lines.append("")
    lines.append(f"{total_errors} error(s), {total_warnings} warning(s)")
    lines.append("RESULT: FAILED" if total_errors else "RESULT: passed")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate the resources file.")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_PATH), help="resources JSON file")
    parser.add_argument("--check-sources", action="store_true",
                        help="download each source_url and check the quotes (needs internet)")
    args = parser.parse_args(argv)

    path = Path(args.path)
    try:
        places = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read {path}: {exc}")
        return 1
    if not isinstance(places, list):
        print(f"{path} must hold a JSON list of places")
        return 1

    results = build_results(places, check_src=args.check_sources)
    print(format_report(results, path, args.check_sources))
    return 1 if any(errors for _label, errors, _warnings in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
