#!/usr/bin/env python3
"""Pre-commit hook: check BigFix BES files for opinionated conventions.

This is the BES-content companion to the `validate-bes` hook. `validate-bes`
only checks that a file is well-formed XML that satisfies the BES.xsd schema --
it says nothing about the *content* being conventional or correct. This tool
goes further, with PICKY, OPINIONATED checks (several AUTO-FIXABLE) that the XSD
cannot express.

A BES file is XML rooted at <BES> holding one or more content objects: Task,
Fixlet, Analysis, ComputerGroup, Baseline, SingleAction, ... The checks are
scoped by what actually makes sense for each: value-format checks apply wherever
the field appears; presence checks apply only to Task/Fixlet (the "publishable"
content -- Analysis and ComputerGroup legitimately have no SourceReleaseDate or
modification time).

Checks:
    E200  an <ActionScript> MIMEType is missing or not one of the allowed set
    E201  a <SourceReleaseDate> is present but not in YYYY-MM-DD format
    E202  an x-fixlet-modification-time value is not in the expected format
          (e.g. `Tue, 14 Jul 2026 18:32:35 +0000`)
    E203  a <DownloadSize> is not empty and not 0-or-a-positive-integer (fixable
          -> 0)
    E204  a Task/Fixlet <Description> still contains the boilerplate placeholder
          "enter a description of the"
    E205  an x-fixlet-cpe23-item-name value is not a valid CPE 2.3 string
    E206  an action-ui-metadata value is not well-formed
    E207  a Description / Relevance / ActionScript entity-escapes a character
          (< > &) that requires <![CDATA[ ... ]]> instead (fixable -> unescaped
          and CDATA-wrapped)
    W200  the file is not parseable BES XML; skipped (advisory -- validate-bes
          is the authority on file validity)
    W201  a Task/Fixlet has no x-fixlet-modification-time MIMEField (fixable ->
          the moment the linter ran)
    W202  a Task/Fixlet has no <SourceReleaseDate> (fixable -> today)
    W203  a Task/Fixlet has DownloadSize > 0 but no download/prefetch keyword in
          any ActionScript
    W204  an <ActionScript> body is not wrapped in <![CDATA[ ... ]]> (fixable,
          but ONLY under --strict, since wrapping already-escaped content can
          change its meaning)
    W205  an <ActionScript> has more than one blank line before </ActionScript>
          (fixable -> collapsed to one)
    W206  a prefetch / "add prefetch item" line does not match the expected shape

The allowed <ActionScript> MIMETypes are:
    application/x-Fixlet-Windows-Shell     (the DEFAULT BigFix ActionScript type
                                            for ALL platforms -- despite the name
                                            it is not Windows-specific)
    application/x-sh                       (shell, e.g. macOS / Linux)
    application/x-AppleScript              (macOS AppleScript)
    application/x-Fixlet-Windows-PowerShell  (Windows-specific PowerShell)
    text/x-uri                             (open a URL)

E-codes are real issues and fail the hook. W-codes are advisory and do NOT fail
the hook unless --strict is given; wire the hook with `verbose: true` to surface
them. W200 is how the tool stays out of validate-bes's lane: an unparseable file
is skipped, not failed, here.

--auto-fix rewrites the fixable conventions in place: an invalid/empty
DownloadSize -> 0 (E203); a missing SourceReleaseDate -> today (W202); a missing
x-fixlet-modification-time -> the moment the linter ran (W201); collapsed blank
lines before </ActionScript> (W205); and a Description/Relevance/ActionScript
that entity-escapes < > & is unescaped and CDATA-wrapped (E207). One fix is
gated behind --strict: wrapping an otherwise-plain ActionScript body in
<![CDATA[ ... ]]> (W204). --auto-fix defaults to yes when files are given
explicitly, but to no when auto-discovering, so a bare run is read-only. An
auto-fixed file fails the hook so the change is reviewed and re-staged.

Usage:
    bigfix_bes_check_conventions.py [--strict] [--auto-fix=yes|no]
        [--disable E200,W201] [file.bes ...]

With no file arguments, all *.bes files in the current folder and below are
checked. --disable takes a comma-separated list of check IDs to skip entirely.

A file can opt out of all checks with an XML comment anywhere in it:
    <!-- pre-commit-skip: bes-conventions -->
or out of a single check family with the matching marker (also anywhere in the
file, e.g. in an XML comment):
    mimetype-ok             (E200)
    source-release-date-ok  (E201 and W202)
    modification-time-ok     (E202 and W201)
    download-size-ok        (E203 and W203)
    description-ok          (E204)
    cpe-ok                  (E205)
    action-ui-metadata-ok   (E206)
    cdata-ok                (W204 and E207)
    action-blank-lines-ok   (W205)
    prefetch-ok             (W206)

Files that look like mustache templates (containing `{{ ... }}`, e.g. the
`*.bes.mustache` sources that ContentFromTemplate renders) are skipped silently:
they are not valid BES XML until rendered, and their own output is what should
be linted.

Exit codes:
    0  no E-code issues and nothing auto-fixed (and, without --strict, regardless
       of warnings)
    1  an E-code issue was found, a file was auto-fixed, or a warning was found
       while --strict is set
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from xml.etree import ElementTree

SKIP_MARKER = "pre-commit-skip: bes-conventions"

# per-check opt-out markers (matched anywhere in the file text)
MIMETYPE_MARKER = "mimetype-ok"  # E200
SOURCE_RELEASE_DATE_MARKER = "source-release-date-ok"  # E201, W202
MODIFICATION_TIME_MARKER = "modification-time-ok"  # E202, W201
DOWNLOAD_SIZE_MARKER = "download-size-ok"  # E203, W203
DESCRIPTION_MARKER = "description-ok"  # E204
CPE_MARKER = "cpe-ok"  # E205
ACTION_UI_METADATA_MARKER = "action-ui-metadata-ok"  # E206
CDATA_MARKER = "cdata-ok"  # W204
ACTION_BLANK_LINES_MARKER = "action-blank-lines-ok"  # W205
PREFETCH_MARKER = "prefetch-ok"  # W206

BES_EXTENSIONS = (".bes",)

# the ActionScript MIMETypes BigFix supports (and this repo allows). The
# "Windows-Shell" one is the platform-agnostic default despite its name; only
# "Windows-PowerShell" is genuinely Windows-only.
ALLOWED_MIMETYPES = frozenset(
    [
        "application/x-Fixlet-Windows-Shell",
        "application/x-sh",
        "application/x-AppleScript",
        "application/x-Fixlet-Windows-PowerShell",
        "text/x-uri",
    ]
)

# content objects that live directly under <BES>
CONTENT_TAGS = frozenset(
    ["Task", "Fixlet", "Analysis", "ComputerGroup", "Baseline", "SingleAction"]
)
# only these are expected to carry a SourceReleaseDate / modification time and to
# have a real (non-placeholder) description and a download action
DATED_CONTENT_TAGS = frozenset(["Task", "Fixlet"])

MODIFICATION_TIME_NAME = "x-fixlet-modification-time"
DESCRIPTION_PLACEHOLDER = "enter a description of the"

# example modification-time value: "Tue, 14 Jul 2026 18:32:35 +0000"
MODIFICATION_TIME_RE = re.compile(
    r"^(?P<dow>[A-Za-z]{3}), \d{2} (?P<mon>[A-Za-z]{3}) \d{4} "
    r"\d{2}:\d{2}:\d{2} [+-]\d{4}$"
)
WEEKDAYS = frozenset(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
MONTHS = frozenset(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
)

SOURCE_RELEASE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOWNLOAD_SIZE_RE = re.compile(r"^\d+$")

# a CPE 2.3 formatted string: cpe:2.3: then 11 colon-separated components
# (part vendor product version update edition language sw_edition target_sw
# target_hw other); colons inside a component are backslash-escaped.
CPE23_RE = re.compile(r"^cpe:2\.3:[aho*\-](:([^:\\]|\\.)+){10}$", re.IGNORECASE)

# action-ui-metadata: the two shapes BigFixSetupTemplateDictionary emits
ACTION_UI_METADATA_RES = (
    re.compile(r'\s*{\s*"version" *: *"\d+(\.\d+)*" *, *"size" *: *\d+ *}'),
    re.compile(
        r'\s*{\s*"version" *: *"\d+(\.\d+)+" *, *"size" *: *\d+ *'
        r'(,"icon":"data:.+"){0,1}\s*}\s*'
    ),
)

# a prefetch statement or an "add prefetch item" line (user-supplied shape)
PREFETCH_OK_RE = re.compile(
    r"(^prefetch \S+ sha1:\S{40} size:\d+ https*:\/\/\S+ sha256:\S{64}$"
    r"|^\s+add prefetch item name=\S+ sha1=\S{40} size=\d+ url=https*:\/\/\S+"
    r" sha256=\S{64}$)"
)
DOWNLOAD_KEYWORD_RE = re.compile(r"prefetch|download|add prefetch item", re.IGNORECASE)

# an entity reference for a character that requires CDATA (or escaping): < > &
# -- either the named entity or a decimal/hex numeric reference to it. A literal
# `>` is valid XML text and does NOT require CDATA, so it is deliberately absent.
SPECIAL_ENTITY_RE = re.compile(
    r"&(?:lt|gt|amp|#0*(?:60|62|38)|#x0*(?:3c|3e|26));", re.IGNORECASE
)
# text elements whose content should be CDATA-wrapped when it has special chars
CDATA_ELEMENT_RE = re.compile(
    r"<(Description|Relevance|ActionScript)\b[^>]*>(.*?)</\1>", re.DOTALL
)
# a real child element open tag (distinguishes an action <Description>, whose
# body is <PreLink>/<Link>/<PostLink> markup, from an entity-escaped text body)
CHILD_ELEMENT_RE = re.compile(r"<[A-Za-z]")

ACTIONSCRIPT_OPEN_RE = re.compile(r"<ActionScript\b([^>]*)>")
ACTIONSCRIPT_FULL_RE = re.compile(
    r"<ActionScript\b([^>]*)>(.*?)</ActionScript>", re.DOTALL
)
MIMETYPE_ATTR_RE = re.compile(r'MIMEType\s*=\s*"([^"]*)"')
SRD_RE = re.compile(r"<SourceReleaseDate>(.*?)</SourceReleaseDate>", re.DOTALL)
DOWNLOAD_SIZE_TAG_RE = re.compile(r"<DownloadSize>(.*?)</DownloadSize>", re.DOTALL)
MODTIME_VALUE_RE = re.compile(
    r"<Name>\s*"
    + re.escape(MODIFICATION_TIME_NAME)
    + r"\s*</Name>\s*<Value>(.*?)</Value>",
    re.DOTALL,
)
NAMED_MIMEFIELD_RE = re.compile(
    r"<Name>\s*([^<]*?)\s*</Name>\s*<Value>(.*?)</Value>", re.DOTALL
)
CONTENT_OPEN_RE = re.compile(r"<(" + "|".join(sorted(CONTENT_TAGS)) + r")\b")
CONTENT_BLOCK_RE = re.compile(r"(<(Task|Fixlet)\b[^>]*>)(.*?)(</\2>)", re.DOTALL)
MUSTACHE_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
CDATA_RE = re.compile(r"^<!\[CDATA\[(.*)\]\]>$", re.DOTALL)
# 2+ blank lines immediately before a </ActionScript> close (an optional CDATA
# terminator may sit between the blank lines and the close tag)
BLANK_BEFORE_CLOSE_RE = re.compile(
    r"(\n)(?:[ \t]*\n){2,}([ \t]*(?:\]\]>)?[ \t]*</ActionScript>)"
)

# where a new SourceReleaseDate / modification-time MIMEField may be inserted so
# the result still satisfies the BES.xsd element ordering
SRD_ANCHORS = (
    "<SourceSeverity",
    "<CVENames",
    "<SANSID",
    "<MIMEField",
    "<Domain",
    "<DefaultAction",
    "<Action",
)
MODTIME_ANCHORS = ("<Domain", "<DefaultAction", "<Action", "<SingleAction")

KNOWN_CODES = frozenset(
    [
        "E200",  # ActionScript MIMEType missing / not allowed
        "E201",  # SourceReleaseDate not YYYY-MM-DD
        "E202",  # modification-time value not in expected format
        "E203",  # DownloadSize not 0-or-positive-integer
        "E204",  # Description contains the boilerplate placeholder
        "E205",  # x-fixlet-cpe23-item-name not a valid CPE 2.3 string
        "E206",  # action-ui-metadata not well-formed
        "E207",  # entity-escaped special chars where CDATA is required
        "W200",  # not parseable BES XML; skipped
        "W201",  # Task/Fixlet missing x-fixlet-modification-time
        "W202",  # Task/Fixlet missing SourceReleaseDate
        "W203",  # DownloadSize > 0 but no download keyword in an ActionScript
        "W204",  # ActionScript body not wrapped in CDATA
        "W205",  # more than one blank line before </ActionScript>
        "W206",  # prefetch / add-prefetch-item line malformed
    ]
)


def _now():
    """Return the current UTC time (isolated so tests can monkeypatch it)."""
    return datetime.now(timezone.utc)


def _today_str(now=None):
    """Return today's date as YYYY-MM-DD."""
    return (now or _now()).strftime("%Y-%m-%d")


def _modtime_str(now=None):
    """Return the current time as e.g. `Tue, 14 Jul 2026 18:32:35 +0000`."""
    return (now or _now()).strftime("%a, %d %b %Y %H:%M:%S %z")


def _lineno(src, pos):
    """Return the 1-based line number of character offset `pos` in `src`."""
    return src.count("\n", 0, pos) + 1


def _strip_cdata(text):
    """Return `text` trimmed, with a wrapping <![CDATA[ ... ]]> removed if present."""
    text = text.strip()
    match = CDATA_RE.match(text)
    return match.group(1).strip() if match else text


def _xml_unescape(text):
    """Decode XML entity references (named and numeric) to their characters.

    `&amp;` is decoded last so `&amp;lt;` becomes the literal text `&lt;`, not `<`.
    """
    text = (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    return text.replace("&amp;", "&")


def _valid_source_release_date(value):
    """True if `value` is a real YYYY-MM-DD date."""
    if not SOURCE_RELEASE_DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _valid_modification_time(value):
    """True if `value` looks like `Tue, 14 Jul 2026 18:32:35 +0000`."""
    match = MODIFICATION_TIME_RE.match(value)
    if not match:
        return False
    if match.group("dow") not in WEEKDAYS or match.group("mon") not in MONTHS:
        return False
    try:
        datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        return False
    return True


def _valid_cpe23(value):
    """True if `value` is a CPE 2.3 formatted string."""
    return bool(CPE23_RE.match(value))


def _valid_action_ui_metadata(value):
    """True if `value` matches one of the two allowed action-ui-metadata shapes."""
    return any(pattern.fullmatch(value) for pattern in ACTION_UI_METADATA_RES)


# --------------------------------------------------------------------------
# checks (each returns a list of (lineno, code, message))
# --------------------------------------------------------------------------


def check_action_mimetypes(src):
    """E200: every <ActionScript> must carry a MIMEType from the allowed set."""
    issues = []
    for match in ACTIONSCRIPT_OPEN_RE.finditer(src):
        attrs = match.group(1)
        lineno = _lineno(src, match.start())
        mime_match = MIMETYPE_ATTR_RE.search(attrs)
        if mime_match is None:
            issues.append(
                (
                    lineno,
                    "E200",
                    "ActionScript has no MIMEType; add one of "
                    f"{sorted(ALLOWED_MIMETYPES)}; add `{MIMETYPE_MARKER}` if intentional",
                )
            )
        elif mime_match.group(1) not in ALLOWED_MIMETYPES:
            issues.append(
                (
                    lineno,
                    "E200",
                    f'ActionScript MIMEType "{mime_match.group(1)}" is not one of '
                    f"{sorted(ALLOWED_MIMETYPES)}; add `{MIMETYPE_MARKER}` if intentional",
                )
            )
    return issues


def check_source_release_date_format(src):
    """E201: every <SourceReleaseDate> present must be YYYY-MM-DD (empty allowed)."""
    issues = []
    for match in SRD_RE.finditer(src):
        value = _strip_cdata(match.group(1))
        if value == "":
            continue
        if not _valid_source_release_date(value):
            issues.append(
                (
                    _lineno(src, match.start()),
                    "E201",
                    f'SourceReleaseDate "{value}" is not in YYYY-MM-DD format; '
                    f"add `{SOURCE_RELEASE_DATE_MARKER}` if intentional",
                )
            )
    return issues


def check_modification_time_format(src):
    """E202: every x-fixlet-modification-time value must match the expected format."""
    issues = []
    for match in MODTIME_VALUE_RE.finditer(src):
        value = _strip_cdata(match.group(1))
        if not _valid_modification_time(value):
            issues.append(
                (
                    _lineno(src, match.start()),
                    "E202",
                    f'x-fixlet-modification-time "{value}" is not in the expected '
                    "format (e.g. `Tue, 14 Jul 2026 18:32:35 +0000`); add "
                    f"`{MODIFICATION_TIME_MARKER}` if intentional",
                )
            )
    return issues


def check_download_size_value(src):
    """E203: every <DownloadSize> present must be 0 or a positive integer."""
    issues = []
    for match in DOWNLOAD_SIZE_TAG_RE.finditer(src):
        value = _strip_cdata(match.group(1))
        if value == "" or not DOWNLOAD_SIZE_RE.match(value):
            issues.append(
                (
                    _lineno(src, match.start()),
                    "E203",
                    f'DownloadSize "{value}" is not 0 or a positive integer; '
                    f"add `{DOWNLOAD_SIZE_MARKER}` if intentional",
                )
            )
    return issues


def check_action_ui_metadata(src):
    """E206: an action-ui-metadata value must be well-formed."""
    issues = []
    for match in NAMED_MIMEFIELD_RE.finditer(src):
        if match.group(1).strip() != "action-ui-metadata":
            continue
        value = _strip_cdata(match.group(2))
        if not _valid_action_ui_metadata(value):
            issues.append(
                (
                    _lineno(src, match.start()),
                    "E206",
                    f'action-ui-metadata "{value}" is not well-formed; add '
                    f"`{ACTION_UI_METADATA_MARKER}` if intentional",
                )
            )
    return issues


def check_cpe23(src):
    """E205: an x-fixlet-cpe23-item-name value must be a valid CPE 2.3 string."""
    issues = []
    for match in NAMED_MIMEFIELD_RE.finditer(src):
        if match.group(1).strip().lower() != "x-fixlet-cpe23-item-name":
            continue
        value = _strip_cdata(match.group(2))
        if not _valid_cpe23(value):
            issues.append(
                (
                    _lineno(src, match.start()),
                    "E205",
                    f'x-fixlet-cpe23-item-name "{value}" is not a valid CPE 2.3 '
                    f"string; add `{CPE_MARKER}` if intentional",
                )
            )
    return issues


def check_cdata_required(src):
    """E207: a Description/Relevance/ActionScript that entity-escapes < > &.

    Such content should use <![CDATA[ ... ]]> instead. Elements already wrapped in
    CDATA are fine, and elements whose body is real child markup (an action's
    <Description> of <PreLink>/<Link>/<PostLink>) are not text bodies at all, so
    both are skipped.
    """
    issues = []
    for match in CDATA_ELEMENT_RE.finditer(src):
        tag, inner = match.group(1), match.group(2)
        if "<![CDATA[" in inner or CHILD_ELEMENT_RE.search(inner):
            continue
        if SPECIAL_ENTITY_RE.search(inner):
            issues.append(
                (
                    _lineno(src, match.start()),
                    "E207",
                    f"{tag} entity-escapes a character (< > &) that requires "
                    f"<![CDATA[ ... ]]>; add `{CDATA_MARKER}` if intentional",
                )
            )
    return issues


def check_actionscript_cdata(src):
    """W204: an ActionScript body should be wrapped in <![CDATA[ ... ]]>.

    Bodies that entity-escape special chars are owned by E207, not warned here.
    """
    issues = []
    for match in ACTIONSCRIPT_FULL_RE.finditer(src):
        body = match.group(2)
        if "<![CDATA[" in body or SPECIAL_ENTITY_RE.search(body):
            continue
        issues.append(
            (
                _lineno(src, match.start()),
                "W204",
                "ActionScript body is not wrapped in <![CDATA[ ... ]]>; add "
                f"`{CDATA_MARKER}` if intentional (auto-fixable under --strict)",
            )
        )
    return issues


def check_actionscript_blank_lines(src):
    """W205: no more than one blank line before </ActionScript>."""
    issues = []
    for match in BLANK_BEFORE_CLOSE_RE.finditer(src):
        issues.append(
            (
                _lineno(src, match.start()),
                "W205",
                "more than one blank line before </ActionScript>; add "
                f"`{ACTION_BLANK_LINES_MARKER}` if intentional",
            )
        )
    return issues


def check_prefetch_lines(src):
    """W206: a prefetch / add-prefetch-item line must match the expected shape."""
    issues = []
    for match in ACTIONSCRIPT_FULL_RE.finditer(src):
        body = match.group(2)
        base = _lineno(src, match.start(2))
        for offset, raw in enumerate(body.splitlines()):
            line = raw.rstrip("\r")
            stripped = line.strip()
            is_prefetch = stripped.startswith("prefetch ")
            is_add = "add prefetch item" in stripped
            if not (is_prefetch or is_add):
                continue
            if PREFETCH_OK_RE.fullmatch(line) or PREFETCH_OK_RE.fullmatch(stripped):
                continue
            issues.append(
                (
                    base + offset,
                    "W206",
                    f'prefetch line "{stripped[:60]}" does not match the expected '
                    f"shape; add `{PREFETCH_MARKER}` if intentional",
                )
            )
    return issues


def _content_objects(root, src):
    """Yield (tag, element, lineno) for each content object directly under <BES>."""
    opens = [
        (match.group(1), _lineno(src, match.start()))
        for match in CONTENT_OPEN_RE.finditer(src)
    ]
    index = 0
    for child in list(root):
        tag = child.tag if isinstance(child.tag, str) else None
        if tag not in CONTENT_TAGS:
            continue
        lineno = 1
        if index < len(opens) and opens[index][0] == tag:
            lineno = opens[index][1]
        index += 1
        yield tag, child, lineno


def _has_modification_time(element):
    """True if `element` has a MIMEField named x-fixlet-modification-time."""
    for mimefield in element.findall("MIMEField"):
        name = mimefield.find("Name")
        if name is not None and (name.text or "").strip() == MODIFICATION_TIME_NAME:
            return True
    return False


def _action_scripts_text(element):
    """Return the concatenated text of every ActionScript under `element`."""
    return "\n".join((a.text or "") for a in element.iter("ActionScript"))


def check_dated_content(root, src, disabled):
    """W201/W202/W203/E204: per-Task/Fixlet presence and description checks."""
    issues = []
    for tag, element, lineno in _content_objects(root, src):
        if tag not in DATED_CONTENT_TAGS:
            continue
        title = element.find("Title")
        label = (title.text or "").strip() if title is not None else ""
        where = f' ("{label}")' if label else ""

        if "W201" not in disabled and not _has_modification_time(element):
            issues.append(
                (
                    lineno,
                    "W201",
                    f"{tag}{where} has no x-fixlet-modification-time MIMEField; add "
                    f"`{MODIFICATION_TIME_MARKER}` if intentional",
                )
            )
        if "W202" not in disabled and element.find("SourceReleaseDate") is None:
            issues.append(
                (
                    lineno,
                    "W202",
                    f"{tag}{where} has no SourceReleaseDate; add "
                    f"`{SOURCE_RELEASE_DATE_MARKER}` if intentional",
                )
            )
        if "E204" not in disabled:
            description = element.find("Description")
            text = "".join(description.itertext()) if description is not None else ""
            if DESCRIPTION_PLACEHOLDER in text.lower():
                issues.append(
                    (
                        lineno,
                        "E204",
                        f"{tag}{where} Description contains the placeholder "
                        f'"{DESCRIPTION_PLACEHOLDER}"; add `{DESCRIPTION_MARKER}` '
                        "if intentional",
                    )
                )
        if "W203" not in disabled:
            download = element.find("DownloadSize")
            raw = _strip_cdata(download.text or "") if download is not None else ""
            size = int(raw) if DOWNLOAD_SIZE_RE.match(raw) else 0
            if size > 0 and not DOWNLOAD_KEYWORD_RE.search(
                _action_scripts_text(element)
            ):
                issues.append(
                    (
                        lineno,
                        "W203",
                        f"{tag}{where} has DownloadSize > 0 but no download/prefetch "
                        f"keyword in any ActionScript; add `{DOWNLOAD_SIZE_MARKER}` "
                        "if intentional",
                    )
                )
    return issues


# --------------------------------------------------------------------------
# auto-fixers (each mutates `src` text and returns (new_src, fixed_list))
# --------------------------------------------------------------------------


def fix_download_size(src):
    """E203: rewrite an empty/invalid <DownloadSize> to 0."""
    fixed = []

    def repl(match):
        value = _strip_cdata(match.group(1))
        if value == "" or not DOWNLOAD_SIZE_RE.match(value):
            fixed.append(
                (
                    _lineno(src, match.start()),
                    "E203",
                    f'DownloadSize "{value}" set to 0',
                )
            )
            return "<DownloadSize>0</DownloadSize>"
        return match.group(0)

    return DOWNLOAD_SIZE_TAG_RE.sub(repl, src), fixed


def fix_blank_lines(src):
    """W205: collapse 2+ blank lines before </ActionScript> to one."""
    fixed = []

    def repl(match):
        fixed.append(
            (
                _lineno(src, match.start()),
                "W205",
                "collapsed blank lines before </ActionScript>",
            )
        )
        return match.group(1) + "\n" + match.group(2)

    return BLANK_BEFORE_CLOSE_RE.sub(repl, src), fixed


def fix_cdata_required(src):
    """E207: unescape a Description/Relevance/ActionScript body and CDATA-wrap it.

    Skips a body whose unescaped text would contain `]]>`, which cannot sit
    inside a single CDATA section -- that one is left to error.
    """
    fixed = []

    def repl(match):
        tag, inner = match.group(1), match.group(2)
        if "<![CDATA[" in inner or CHILD_ELEMENT_RE.search(inner):
            return match.group(0)
        if not SPECIAL_ENTITY_RE.search(inner):
            return match.group(0)
        decoded = _xml_unescape(inner)
        if "]]>" in decoded:
            return match.group(0)
        fixed.append(
            (
                _lineno(src, match.start()),
                "E207",
                f"wrapped {tag} body in <![CDATA[ ... ]]>",
            )
        )
        open_tag = match.group(0)[: match.start(2) - match.start()]
        return f"{open_tag}<![CDATA[{decoded}]]></{tag}>"

    return CDATA_ELEMENT_RE.sub(repl, src), fixed


def fix_actionscript_cdata(src):
    """W204: wrap an un-wrapped ActionScript body in <![CDATA[ ... ]]>.

    Skips a body that already contains a `]]>` sequence, which cannot be placed
    inside a CDATA section without splitting -- that one is left to warn.
    """
    fixed = []

    def repl(match):
        attrs, body = match.group(1), match.group(2)
        if "<![CDATA[" in body or "]]>" in body:
            return match.group(0)
        fixed.append(
            (
                _lineno(src, match.start()),
                "W204",
                "wrapped ActionScript body in <![CDATA[ ... ]]>",
            )
        )
        return f"<ActionScript{attrs}><![CDATA[{body}]]></ActionScript>"

    return ACTIONSCRIPT_FULL_RE.sub(repl, src), fixed


def _detect_indent(inner):
    """Return the indentation of the first element inside a content block."""
    match = re.search(r"\n([ \t]+)<\w", inner)
    return match.group(1) if match else "\t\t"


def _insert_ordered(inner, new_text, anchors):
    """Insert `new_text` (a full indented line) before the first anchor found."""
    for anchor in anchors:
        pos = inner.find(anchor)
        if pos != -1:
            line_start = inner.rfind("\n", 0, pos) + 1
            return inner[:line_start] + new_text + inner[line_start:]
    stripped = inner.rstrip()
    trailing = inner[len(stripped) :]
    return stripped + "\n" + new_text + (trailing or "\n")


def fix_missing_dates(src, now=None, fix_srd=True, fix_modtime=True):
    """W201/W202: insert a missing SourceReleaseDate / modification time.

    Values use the moment the linter ran. Insertion positions keep the BES.xsd
    element ordering (SourceReleaseDate in the metadata block; the modification
    time MIMEField before <Domain>). `fix_srd` / `fix_modtime` gate each insert
    independently so a single per-field opt-out marker only suppresses its own.
    """
    fixed = []
    date = _today_str(now)
    modtime = _modtime_str(now)

    def repl(match):
        open_tag, tag, inner, close = match.groups()
        indent = _detect_indent(inner)
        lineno = _lineno(src, match.start())
        if fix_srd and "<SourceReleaseDate" not in inner:
            inner = _insert_ordered(
                inner,
                f"{indent}<SourceReleaseDate>{date}</SourceReleaseDate>\n",
                SRD_ANCHORS,
            )
            fixed.append((lineno, "W202", f"inserted SourceReleaseDate {date}"))
        if fix_modtime and MODIFICATION_TIME_NAME not in inner:
            block = (
                f"{indent}<MIMEField>\n"
                f"{indent}\t<Name>{MODIFICATION_TIME_NAME}</Name>\n"
                f"{indent}\t<Value>{modtime}</Value>\n"
                f"{indent}</MIMEField>\n"
            )
            inner = _insert_ordered(inner, block, MODTIME_ANCHORS)
            fixed.append((lineno, "W201", "inserted x-fixlet-modification-time"))
        return open_tag + inner + close

    return CONTENT_BLOCK_RE.sub(repl, src), fixed


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def check_file(path, disabled=frozenset(), strict=False, auto_fix=False, now=None):
    """Check one BES file; return (issues, fixed).

    Each of `issues` and `fixed` is a list of (lineno, code, message). A file is
    skipped (returns [], []) when it carries the file-level skip marker or looks
    like a mustache template. A file that will not parse as XML yields a single
    advisory W200 (validate-bes owns file validity) and no other checks.

    When `auto_fix` is set, the fixable conventions are rewritten in place and
    reported under `fixed`; the CDATA wrap (W204) is applied only when `strict`
    is also set. The file is then re-read so the checks below do not re-report
    what was just fixed.
    """
    if not os.path.isfile(path):
        return [(1, "W200", "file not found; skipping")], []

    with open(path, encoding="utf-8", errors="replace") as handle:
        src = handle.read()

    if SKIP_MARKER in src:
        return [], []
    if MUSTACHE_RE.search(src):
        return [], []

    try:
        ElementTree.fromstring(src)
    except ElementTree.ParseError as err:
        return [(1, "W200", f"not parseable BES XML ({err}); skipping")], []

    fixed = []
    if auto_fix:
        new_src = src
        if "E203" not in disabled and DOWNLOAD_SIZE_MARKER not in new_src:
            new_src, got = fix_download_size(new_src)
            fixed += got
        if "W205" not in disabled and ACTION_BLANK_LINES_MARKER not in new_src:
            new_src, got = fix_blank_lines(new_src)
            fixed += got
        if "E207" not in disabled and CDATA_MARKER not in new_src:
            new_src, got = fix_cdata_required(new_src)
            fixed += got
        if strict and "W204" not in disabled and CDATA_MARKER not in new_src:
            new_src, got = fix_actionscript_cdata(new_src)
            fixed += got
        fix_srd = "W202" not in disabled and SOURCE_RELEASE_DATE_MARKER not in new_src
        fix_modtime = "W201" not in disabled and MODIFICATION_TIME_MARKER not in new_src
        if fix_srd or fix_modtime:
            new_src, got = fix_missing_dates(
                new_src, now, fix_srd=fix_srd, fix_modtime=fix_modtime
            )
            fixed += got
        if new_src != src:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(new_src)
            src = new_src

    # re-parse the (possibly rewritten) source for the read-only checks
    try:
        root = ElementTree.fromstring(src)
    except ElementTree.ParseError as err:
        return [(1, "W200", f"not parseable BES XML after fixes ({err})")], fixed

    issues = []
    if "E200" not in disabled and MIMETYPE_MARKER not in src:
        issues += check_action_mimetypes(src)
    if "E201" not in disabled and SOURCE_RELEASE_DATE_MARKER not in src:
        issues += check_source_release_date_format(src)
    if "E202" not in disabled and MODIFICATION_TIME_MARKER not in src:
        issues += check_modification_time_format(src)
    if "E203" not in disabled and DOWNLOAD_SIZE_MARKER not in src:
        issues += check_download_size_value(src)
    if "E205" not in disabled and CPE_MARKER not in src:
        issues += check_cpe23(src)
    if "E206" not in disabled and ACTION_UI_METADATA_MARKER not in src:
        issues += check_action_ui_metadata(src)
    if "E207" not in disabled and CDATA_MARKER not in src:
        issues += check_cdata_required(src)
    if "W204" not in disabled and CDATA_MARKER not in src:
        issues += check_actionscript_cdata(src)
    if "W205" not in disabled and ACTION_BLANK_LINES_MARKER not in src:
        issues += check_actionscript_blank_lines(src)
    if "W206" not in disabled and PREFETCH_MARKER not in src:
        issues += check_prefetch_lines(src)

    presence_disabled = set(disabled)
    if SOURCE_RELEASE_DATE_MARKER in src:
        presence_disabled.add("W202")
    if MODIFICATION_TIME_MARKER in src:
        presence_disabled.add("W201")
    if DOWNLOAD_SIZE_MARKER in src:
        presence_disabled.add("W203")
    if DESCRIPTION_MARKER in src:
        presence_disabled.add("E204")
    if {"W201", "W202", "W203", "E204"} - presence_disabled:
        issues += check_dated_content(root, src, presence_disabled)

    return sorted(issues), fixed


def is_bes_file(path):
    """True if `path` has a recognized BES extension."""
    return path.endswith(BES_EXTENSIONS)


def check_files(paths, disabled=frozenset(), strict=False, auto_fix=False):
    """Check several BES files; return a list of (path, issues, fixed) tuples.

    Non-BES paths are skipped. Disabled codes are filtered from the results.
    This is the programmatic entry point: it does no printing.
    """
    results = []
    for path in paths:
        if not is_bes_file(path):
            continue
        issues, fixed = check_file(
            path, disabled=disabled, strict=strict, auto_fix=auto_fix
        )
        issues = [item for item in issues if item[1] not in disabled]
        fixed = [item for item in fixed if item[1] not in disabled]
        results.append((path, issues, fixed))
    return results


def discover_bes_files(root="."):
    """Return all BES files under `root`, pruning hidden and noise directories."""
    skip_dirs = {"__pycache__", "node_modules"}
    root = os.path.normpath(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in skip_dirs
        ]
        for name in filenames:
            if is_bes_file(name):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def main(argv=None):
    """Execution starts here.

    argv defaults to None so this works both as a console_scripts entry point
    (pre-commit calls it with no arguments; argparse then reads sys.argv) and
    when called directly as `main(sys.argv[1:])`.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as failures (non-zero exit) and enable the CDATA "
        "auto-fix (W204); default: advisory",
    )
    parser.add_argument(
        "--auto-fix",
        choices=["yes", "no"],
        default=None,
        help="rewrite fixable conventions in place (default: yes when files are "
        "given, no when auto-discovering)",
    )
    parser.add_argument(
        "--disable",
        default="",
        metavar="CODES",
        help="comma-separated check IDs to skip entirely, e.g. --disable W204",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="BES files to check; if omitted, all *.bes files in the current "
        "folder and below are checked",
    )
    args = parser.parse_args(argv)

    disabled = {
        code.strip().upper() for code in args.disable.split(",") if code.strip()
    }
    unknown = disabled - KNOWN_CODES
    if unknown:
        print(
            f"warning: ignoring unknown --disable code(s): {', '.join(sorted(unknown))}"
        )

    # auto-fix defaults to yes for explicit files, no when auto-discovering; an
    # explicit --auto-fix always wins.
    discovering = not args.files
    if args.auto_fix is not None:
        auto_fix = args.auto_fix == "yes"
    else:
        auto_fix = not discovering
    paths = args.files if args.files else discover_bes_files(".")

    issue_count = 0
    warning_count = 0
    fix_count = 0
    for path, issues, fixed in check_files(
        paths, disabled=disabled, strict=args.strict, auto_fix=auto_fix
    ):
        for lineno, check_id, message in fixed:
            fix_count += 1
            print(f"{path}:{lineno}: [{check_id}] auto-fixed: {message}")
        for lineno, check_id, message in issues:
            if check_id.startswith("W"):
                warning_count += 1
                print(f"{path}:{lineno}: [{check_id}] warning: {message}")
            else:
                issue_count += 1
                print(f"{path}:{lineno}: [{check_id}] {message}")

    if fix_count:
        print(f"\nauto-fixed {fix_count} issue(s); review and re-stage the changes.")
    if warning_count:
        print(f"{warning_count} BES-convention warning(s).")
    if issue_count:
        print(f"{issue_count} BES-convention issue(s).")
    # E-codes and any fix always fail; warnings fail only under --strict
    return 1 if (issue_count or fix_count or (warning_count and args.strict)) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
