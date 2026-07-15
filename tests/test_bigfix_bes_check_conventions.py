#!/usr/bin/env python3
"""Tests for pre_commit_hooks/bigfix_bes_check_conventions.py.

These exercise the BES content checks (E200-E206, W200-W206), the auto-fixers
(DownloadSize, missing dates, blank-line collapse, CDATA wrap), the file-level
and per-family opt-out markers, the mustache-template skip, and main()'s exit
codes.
"""

from datetime import datetime, timezone

import pytest

from pre_commit_hooks import bigfix_bes_check_conventions as checker

FIXED_NOW = datetime(2026, 7, 14, 18, 32, 35, tzinfo=timezone.utc)


def task(
    title="Example",
    description="A real description of what this does.",
    download_size="0",
    srd="2026-07-14",
    modtime="Tue, 14 Jul 2026 18:32:35 +0000",
    mimetype="application/x-Fixlet-Windows-Shell",
    body="\necho hi\n",
    cdata=True,
    extra_mimefields=(),
    marker=None,
):
    """Build a well-formed single-Task BES document.

    srd / modtime = None omit that field. `cdata` wraps the ActionScript body.
    `extra_mimefields` is an iterable of (name, value). `marker` inserts an XML
    comment right after <BES> (used to exercise opt-out markers).
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<BES xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="BES.xsd">',
    ]
    if marker:
        lines.append(f"\t<!-- {marker} -->")
    lines.append("\t<Task>")
    lines.append(f"\t\t<Title>{title}</Title>")
    lines.append(f"\t\t<Description><![CDATA[{description}]]></Description>")
    lines.append("\t\t<Relevance>true</Relevance>")
    lines.append(f"\t\t<DownloadSize>{download_size}</DownloadSize>")
    lines.append("\t\t<Source>test</Source>")
    if srd is not None:
        lines.append(f"\t\t<SourceReleaseDate>{srd}</SourceReleaseDate>")
    if modtime is not None:
        lines.append("\t\t<MIMEField>")
        lines.append("\t\t\t<Name>x-fixlet-modification-time</Name>")
        lines.append(f"\t\t\t<Value>{modtime}</Value>")
        lines.append("\t\t</MIMEField>")
    for name, value in extra_mimefields:
        lines.append("\t\t<MIMEField>")
        lines.append(f"\t\t\t<Name>{name}</Name>")
        lines.append(f"\t\t\t<Value>{value}</Value>")
        lines.append("\t\t</MIMEField>")
    lines.append("\t\t<Domain>BESC</Domain>")
    lines.append('\t\t<DefaultAction ID="Action1">')
    action_body = f"<![CDATA[{body}]]>" if cdata else body
    lines.append(
        f'\t\t\t<ActionScript MIMEType="{mimetype}">{action_body}</ActionScript>'
    )
    lines.append('\t\t\t<SuccessCriteria Option="OriginalRelevance"></SuccessCriteria>')
    lines.append("\t\t</DefaultAction>")
    lines.append("\t</Task>")
    lines.append("</BES>")
    return "\n".join(lines) + "\n"


def write(tmp_path, name, content):
    """Write `content` to tmp_path/name with CRLF endings; return the path str.

    BES files must be CRLF (E208), so fixtures are written CRLF by default;
    tests that exercise line endings write raw bytes via write_bytes directly.
    """
    path = tmp_path / name
    crlf = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    path.write_bytes(crlf.encode("utf-8"))
    return str(path)


def codes(tmp_path, content, name="x.bes", disabled=frozenset()):
    """Return the sorted set of check codes flagged for `content` (read-only)."""
    path = write(tmp_path, name, content)
    issues, _ = checker.check_file(path, disabled)
    return sorted({item[1] for item in issues})


def autofix(tmp_path, content, name="x.bes", strict=False):
    """Run check_file with auto_fix and a fixed clock; return (rewritten, fixed)."""
    path = write(tmp_path, name, content)
    _, fixed = checker.check_file(path, strict=strict, auto_fix=True, now=FIXED_NOW)
    return (tmp_path / name).read_text(encoding="utf-8"), fixed


# --- clean baseline -------------------------------------------------------


def test_good_task_is_clean(tmp_path):
    assert codes(tmp_path, task()) == []


@pytest.mark.parametrize(
    "mimetype",
    [
        "application/x-Fixlet-Windows-Shell",
        "application/x-sh",
        "application/x-AppleScript",
        "application/x-Fixlet-Windows-PowerShell",
        "text/x-uri",
    ],
)
def test_all_allowed_mimetypes_clean(tmp_path, mimetype):
    assert codes(tmp_path, task(mimetype=mimetype)) == []


# --- E200 ActionScript MIMEType ------------------------------------------


def test_e200_disallowed_mimetype(tmp_path):
    assert "E200" in codes(tmp_path, task(mimetype="application/x-python"))


def test_e200_marker_opts_out(tmp_path):
    assert "E200" not in codes(
        tmp_path, task(mimetype="application/x-python", marker="mimetype-ok")
    )


# --- E201 / E202 formats --------------------------------------------------


@pytest.mark.parametrize("bad", ["07/14/2026", "2026-7-14", "2026/07/14", "2026-13-01"])
def test_e201_bad_dates(tmp_path, bad):
    assert "E201" in codes(tmp_path, task(srd=bad))


@pytest.mark.parametrize(
    "bad",
    [
        "2026-07-14 18:32:35",
        "Fri, 19 Jan 2018 15:45:57 0800",
        "Xyz, 14 Jul 2026 18:32:35 +0000",
        "Tue, 14 Zzz 2026 18:32:35 +0000",
    ],
)
def test_e202_bad_modtime(tmp_path, bad):
    assert "E202" in codes(tmp_path, task(modtime=bad))


# --- E203 / W203 DownloadSize --------------------------------------------


@pytest.mark.parametrize("bad", ["", "-5", "1.5", "abc", "0x10"])
def test_e203_bad_download_size(tmp_path, bad):
    assert "E203" in codes(tmp_path, task(download_size=bad))


@pytest.mark.parametrize("good", ["0", "5", "104632873"])
def test_e203_good_download_size(tmp_path, good):
    assert "E203" not in codes(tmp_path, task(download_size=good))


def test_e203_autofix_to_zero(tmp_path):
    out, fixed = autofix(tmp_path, task(download_size=""))
    assert "<DownloadSize>0</DownloadSize>" in out
    assert any(code == "E203" for _, code, _ in fixed)


def test_w203_download_without_action(tmp_path):
    # DownloadSize > 0 but the ActionScript has no download/prefetch keyword
    assert "W203" in codes(tmp_path, task(download_size="1234", body="\necho hi\n"))


def test_w203_download_with_prefetch_ok(tmp_path):
    body = "\nprefetch x.pkg sha1:{} size:10 https://e/x sha256:{}\ninstaller\n".format(
        "a" * 40,
        "b" * 64,
    )
    assert "W203" not in codes(tmp_path, task(download_size="1234", body=body))


def test_w203_marker_opts_out(tmp_path):
    assert "W203" not in codes(
        tmp_path, task(download_size="1234", marker="download-size-ok")
    )


# --- E204 Description placeholder -----------------------------------------


def test_e204_placeholder(tmp_path):
    assert "E204" in codes(
        tmp_path, task(description="Enter a description of the Task here.")
    )


def test_e204_marker_opts_out(tmp_path):
    assert "E204" not in codes(
        tmp_path,
        task(
            description="Enter a description of the Task here.", marker="description-ok"
        ),
    )


# --- E205 CPE 2.3 ---------------------------------------------------------


def test_e205_valid_cpe_clean(tmp_path):
    cpe = "cpe:2.3:a:microsoft:auto_update:4.83:*:*:*:*:macos:*:*"
    assert "E205" not in codes(
        tmp_path, task(extra_mimefields=[("x-fixlet-cpe23-item-name", cpe)])
    )


@pytest.mark.parametrize(
    "bad",
    [
        "cpe:/a:microsoft:auto_update",  # 2.2 URI, not 2.3
        "microsoft:auto_update:4.83",
        "cpe:2.3:a:microsoft",  # too few components
    ],
)
def test_e205_invalid_cpe(tmp_path, bad):
    assert "E205" in codes(
        tmp_path, task(extra_mimefields=[("x-fixlet-cpe23-item-name", bad)])
    )


def test_e205_case_insensitive_name(tmp_path):
    assert "E205" in codes(
        tmp_path, task(extra_mimefields=[("X-Fixlet-CPE23-Item-Name", "nope")])
    )


# --- E206 action-ui-metadata ---------------------------------------------


@pytest.mark.parametrize(
    "good",
    [
        '{ "version":"4.83","size":4417205 }',
        '{"version": "1.0.0", "size": 10}',
        '{ "version":"1.2","size":10,"icon":"data:image/png;base64,AAAA" }',
    ],
)
def test_e206_valid_metadata(tmp_path, good):
    assert "E206" not in codes(
        tmp_path, task(extra_mimefields=[("action-ui-metadata", good)])
    )


@pytest.mark.parametrize(
    "bad",
    [
        '{ "version":"1.0","size":"10" }',  # size quoted
        '{ "size":10,"version":"1.0" }',  # wrong order
        "not json at all",
    ],
)
def test_e206_invalid_metadata(tmp_path, bad):
    assert "E206" in codes(
        tmp_path, task(extra_mimefields=[("action-ui-metadata", bad)])
    )


# --- W204 CDATA -----------------------------------------------------------


def test_w204_no_cdata(tmp_path):
    assert "W204" in codes(tmp_path, task(cdata=False))


def test_w204_marker_opts_out(tmp_path):
    assert "W204" not in codes(tmp_path, task(cdata=False, marker="cdata-ok"))


def test_w204_autofix_only_under_strict(tmp_path):
    # without --strict, auto-fix leaves the body unwrapped
    out, fixed = autofix(tmp_path, task(cdata=False), strict=False)
    assert "<![CDATA[" not in out.split("<ActionScript")[1].split("</ActionScript>")[0]
    assert not any(code == "W204" for _, code, _ in fixed)


def test_w204_autofix_wraps_under_strict(tmp_path):
    out, fixed = autofix(tmp_path, task(cdata=False), strict=True)
    segment = out.split("<ActionScript")[1].split("</ActionScript>")[0]
    assert "<![CDATA[" in segment and "]]>" in segment
    assert any(code == "W204" for _, code, _ in fixed)


# --- E207 CDATA-required (entity-escaped special chars) -------------------


def test_e207_relevance_entity_escaped(tmp_path):
    content = task().replace(
        "<Relevance>true</Relevance>",
        "<Relevance>exists x whose (a &lt; b)</Relevance>",
    )
    assert "E207" in codes(tmp_path, content)


def test_e207_actionscript_entity_amp(tmp_path):
    # &amp; in a non-CDATA ActionScript -> E207 (and NOT also W204)
    got = codes(tmp_path, task(body="echo a &amp;&amp; b", cdata=False))
    assert "E207" in got and "W204" not in got


def test_e207_literal_gt_not_flagged(tmp_path):
    # a literal > is valid XML text and does not require CDATA
    content = task().replace(
        "<Relevance>true</Relevance>", "<Relevance>a > b</Relevance>"
    )
    assert "E207" not in codes(tmp_path, content)


def test_e207_cdata_body_clean(tmp_path):
    content = task().replace(
        "<Relevance>true</Relevance>",
        "<Relevance><![CDATA[exists x whose (a < b)]]></Relevance>",
    )
    assert "E207" not in codes(tmp_path, content)


def test_e207_action_description_markup_not_flagged(tmp_path):
    # the DefaultAction <Description> is <PreLink>/<Link>/<PostLink> markup, not
    # an entity-escaped text body, even if a PostLink contains &amp;
    content = task().replace(
        '<DefaultAction ID="Action1">',
        '<DefaultAction ID="Action1"><Description><PreLink>Click </PreLink>'
        "<Link>here</Link><PostLink> to run A &amp; B.</PostLink></Description>",
    )
    assert "E207" not in codes(tmp_path, content)


def test_e207_marker_opts_out(tmp_path):
    content = task(marker="cdata-ok").replace(
        "<Relevance>true</Relevance>", "<Relevance>a &lt; b</Relevance>"
    )
    assert "E207" not in codes(tmp_path, content)


def test_e207_autofix_unescapes_and_wraps(tmp_path):
    content = task().replace(
        "<Relevance>true</Relevance>",
        "<Relevance>version of it &gt;= &quot;1.0&quot; &amp; exists x</Relevance>",
    )
    out, fixed = autofix(tmp_path, content)
    assert '<Relevance><![CDATA[version of it >= "1.0" & exists x]]></Relevance>' in out
    assert any(code == "E207" for _, code, _ in fixed)
    assert "E207" not in codes(tmp_path, out, name="after.bes")


def test_e207_autofix_is_not_strict_gated(tmp_path):
    # unlike W204, the E207 wrap happens whenever auto-fix is on (no --strict)
    content = task(body="echo a &amp; b", cdata=False)
    out, fixed = autofix(tmp_path, content, strict=False)
    assert any(code == "E207" for _, code, _ in fixed)
    assert "<![CDATA[echo a & b]]>" in out


# --- W205 blank lines before </ActionScript> ------------------------------


def test_w205_multiple_blank_lines(tmp_path):
    assert "W205" in codes(tmp_path, task(body="\necho hi\n\n\n"))


def test_w205_single_blank_line_ok(tmp_path):
    assert "W205" not in codes(tmp_path, task(body="\necho hi\n\n"))


def test_w205_autofix_collapses(tmp_path):
    out, fixed = autofix(tmp_path, task(body="\necho hi\n\n\n\n"))
    assert any(code == "W205" for _, code, _ in fixed)
    assert "W205" not in codes(tmp_path, out, name="after.bes")


# --- W206 prefetch shape --------------------------------------------------


def test_w206_valid_prefetch_statement(tmp_path):
    body = "\nprefetch x.pkg sha1:{} size:10 https://e/x sha256:{}\n".format(
        "a" * 40,
        "b" * 64,
    )
    assert "W206" not in codes(tmp_path, task(download_size="10", body=body))


def test_w206_valid_add_prefetch_item(tmp_path):
    body = (
        "\n\tadd prefetch item name=x.pkg sha1=%s size=10 url=https://e/x sha256=%s\n"
        % (
            "a" * 40,
            "b" * 64,
        )
    )
    assert "W206" not in codes(tmp_path, task(download_size="10", body=body))


def test_w206_malformed_prefetch(tmp_path):
    # missing the sha256 field
    body = "\nprefetch x.pkg sha1:%s size:10 https://e/x\n" % ("a" * 40)
    assert "W206" in codes(tmp_path, task(download_size="10", body=body))


def test_w206_marker_opts_out(tmp_path):
    body = "\nprefetch x.pkg sha1:%s size:10 https://e/x\n" % ("a" * 40)
    assert "W206" not in codes(
        tmp_path, task(download_size="10", body=body, marker="prefetch-ok")
    )


# --- W201 / W202 presence + auto-insert -----------------------------------


def test_w201_missing_modification_time(tmp_path):
    assert "W201" in codes(tmp_path, task(modtime=None))


def test_w202_missing_source_release_date(tmp_path):
    assert "W202" in codes(tmp_path, task(srd=None))


def test_autofix_inserts_missing_dates(tmp_path):
    out, fixed = autofix(tmp_path, task(srd=None, modtime=None))
    assert "<SourceReleaseDate>2026-07-14</SourceReleaseDate>" in out
    assert "Tue, 14 Jul 2026 18:32:35 +0000" in out
    assert {"W201", "W202"} <= {code for _, code, _ in fixed}
    # and the rewritten file is now clean of those warnings
    assert not ({"W201", "W202"} & set(codes(tmp_path, out, name="after.bes")))


def test_autofix_dates_respects_single_marker(tmp_path):
    # a lone source-release-date-ok must not suppress the modtime insert
    content = task(srd=None, modtime=None).replace(
        "\t<Task>", "\t<!-- source-release-date-ok -->\n\t<Task>", 1
    )
    out, fixed = autofix(tmp_path, content)
    assert "<SourceReleaseDate>" not in out
    assert "x-fixlet-modification-time" in out
    assert {code for _, code, _ in fixed} == {"W201"}


def test_analysis_exempt_from_presence(tmp_path):
    analysis = """<?xml version="1.0" encoding="UTF-8"?>
<BES xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="BES.xsd">
\t<Analysis>
\t\t<Title>Some analysis</Title>
\t\t<Property Name="X">whose (it) of it</Property>
\t</Analysis>
</BES>
"""
    assert codes(tmp_path, analysis) == []


# --- multiple content objects treated as independent entities -------------


def two_objects(first, second):
    """Wrap two content-object blocks (each without the <?xml>/<BES> shell)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<BES xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="BES.xsd">\n'
        f"{first}\n{second}\n</BES>\n"
    )


def _inner(content):
    """Return just the <Task>...</Task> block from a task() document."""
    return content[
        content.index("\t<Task>") : content.index("</Task>") + len("</Task>")
    ]


def test_fault_in_one_object_fails_whole_file(tmp_path):
    # a clean Fixlet next to a Task with a bad MIMEType -> E200 still raised
    clean = _inner(task()).replace("<Task>", "<Fixlet>").replace("</Task>", "</Fixlet>")
    bad = _inner(task(mimetype="application/x-python"))
    assert "E200" in codes(tmp_path, two_objects(clean, bad))


def test_each_object_flagged_separately(tmp_path):
    # first object: bad date; second object: bad mimetype -> both codes present
    a = _inner(task(srd="07/14/2026"))
    b = _inner(task(mimetype="application/x-python"))
    assert set(codes(tmp_path, two_objects(a, b))) >= {"E200", "E201"}


def test_marker_in_one_object_does_not_leak_to_sibling(tmp_path):
    # Task A opts out of the date check inside its own block; Task B still flagged
    a = _inner(task(srd=None)).replace(
        "<Title>Example</Title>",
        "<Title>A</Title>\n\t\t<!-- source-release-date-ok -->",
    )
    b = _inner(task(srd=None)).replace("<Title>Example</Title>", "<Title>B</Title>")
    got = codes(tmp_path, two_objects(a, b))
    assert "W202" in got  # Task B is still flagged


def test_marker_outside_all_objects_is_file_level(tmp_path):
    a = _inner(task(srd=None))
    b = _inner(task(srd=None))
    doc = two_objects(a, b).replace(
        "<BES ", "<!-- source-release-date-ok -->\n<BES ", 1
    )
    assert "W202" not in codes(tmp_path, doc)


def test_autofix_scopes_marker_per_object(tmp_path):
    # Task A opts out (keep it un-dated); Task B gets its dates inserted
    a = _inner(task(srd=None, modtime=None)).replace(
        "<Title>Example</Title>",
        "<Title>A</Title>\n\t\t<!-- source-release-date-ok --><!-- modification-time-ok -->",
    )
    b = _inner(task(srd=None, modtime=None)).replace(
        "<Title>Example</Title>", "<Title>B</Title>"
    )
    out, fixed = autofix(tmp_path, two_objects(a, b))
    # exactly one SourceReleaseDate / modtime inserted (for B, not A)
    assert out.count("<SourceReleaseDate>") == 1
    assert out.count("x-fixlet-modification-time") == 1
    assert {code for _, code, _ in fixed} == {"W201", "W202"}


# --- E208 CRLF line endings ------------------------------------------------


def test_crlf_file_is_clean(tmp_path):
    # write() already writes CRLF, so a good task has no E208
    assert "E208" not in codes(tmp_path, task())


def test_lf_file_flagged(tmp_path):
    path = tmp_path / "lf.bes"
    lf = task().replace("\r\n", "\n").replace("\r", "\n")  # force pure LF
    path.write_bytes(lf.encode("utf-8"))
    issues, _ = checker.check_file(str(path))
    assert "E208" in {code for _, code, _ in issues}


def test_mixed_endings_flagged(tmp_path):
    path = tmp_path / "mixed.bes"
    body = task()
    lf = body.replace("\r\n", "\n").replace("\r", "\n")
    # make only the first newline a CRLF, rest LF -> mixed
    mixed = lf.replace("\n", "\r\n", 1)
    path.write_bytes(mixed.encode("utf-8"))
    issues, _ = checker.check_file(str(path))
    assert "E208" in {code for _, code, _ in issues}


def test_autofix_normalizes_to_crlf(tmp_path):
    path = tmp_path / "lf.bes"
    lf = task().replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(lf.encode("utf-8"))
    _, fixed = checker.check_file(str(path), auto_fix=True, now=FIXED_NOW)
    out = path.read_bytes()
    assert out.count(b"\n") == out.count(b"\r\n")  # every LF is part of a CRLF
    assert b"\r\n" in out
    assert any(code == "E208" for _, code, _ in fixed)


def test_autofix_makes_whole_file_crlf_after_content_fix(tmp_path):
    # an LF file that also needs a content fix ends up entirely CRLF
    path = tmp_path / "lf2.bes"
    lf = task(download_size="").replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(lf.encode("utf-8"))
    _, fixed = checker.check_file(str(path), auto_fix=True, now=FIXED_NOW)
    out = path.read_bytes()
    assert out.count(b"\n") == out.count(b"\r\n")
    assert b"<DownloadSize>0</DownloadSize>" in out
    assert {"E203", "E208"} <= {code for _, code, _ in fixed}


def test_already_crlf_autofix_is_noop(tmp_path):
    path = tmp_path / "crlf.bes"
    crlf = task().replace("\r\n", "\n").replace("\n", "\r\n")
    before = crlf.encode("utf-8")
    path.write_bytes(before)
    _, fixed = checker.check_file(str(path), auto_fix=True, now=FIXED_NOW)
    assert path.read_bytes() == before  # unchanged
    assert not any(code == "E208" for _, code, _ in fixed)


def test_disable_e208_skips(tmp_path):
    path = tmp_path / "lf.bes"
    lf = task().replace("\r\n", "\n").replace("\r", "\n")
    path.write_bytes(lf.encode("utf-8"))
    issues, _ = checker.check_file(str(path), disabled={"E208"})
    assert "E208" not in {code for _, code, _ in issues}


# --- skips ----------------------------------------------------------------


def test_file_skip_marker(tmp_path):
    content = task(mimetype="application/x-python").replace(
        "\t<Task>", "\t<!-- pre-commit-skip: bes-conventions -->\n\t<Task>", 1
    )
    assert codes(tmp_path, content) == []


def test_mustache_template_skipped(tmp_path):
    tmpl = task().replace("<Title>Example</Title>", "<Title>{{DisplayName}}</Title>")
    assert codes(tmp_path, tmpl) == []


def test_unparseable_xml_is_w200_only(tmp_path):
    assert codes(tmp_path, "<BES><Task><Unclosed></Task></BES>") == ["W200"]


def test_non_bes_extension_skipped(tmp_path):
    path = write(tmp_path, "notbes.txt", task(mimetype="application/x-python"))
    assert checker.check_files([path]) == []


# --- autofix output stays XSD-valid (needs validate_bes_xml) --------------


def test_autofixed_dates_are_xsd_valid(tmp_path):
    validate = pytest.importorskip("validate_bes_xml")
    from pathlib import Path

    example = (Path(__file__).parent / "examples" / "example-test.bes").read_text(
        encoding="utf-8"
    )
    # strip the existing SourceReleaseDate and modification-time so the fixer
    # has to re-insert them, then confirm the result still validates
    import re

    example = re.sub(r"\s*<SourceReleaseDate>.*?</SourceReleaseDate>", "", example)
    example = re.sub(
        r"\s*<MIMEField>\s*<Name>x-fixlet-modification-time</Name>.*?</MIMEField>",
        "",
        example,
        flags=re.DOTALL,
    )
    path = write(tmp_path, "fixme.bes", example)
    checker.check_file(path, auto_fix=True, now=FIXED_NOW)
    assert validate.validate_xml(path)


# --- main() exit codes ----------------------------------------------------


def test_main_clean_returns_zero(tmp_path):
    good = write(tmp_path, "good.bes", task())
    assert checker.main([good]) == 0


def test_main_error_returns_one(tmp_path):
    bad = write(tmp_path, "bad.bes", task(mimetype="application/x-python"))
    assert checker.main([bad]) == 1


def test_main_warning_only_zero_without_strict_no_autofix(tmp_path):
    warn = write(tmp_path, "warn.bes", task(srd=None))
    assert checker.main(["--auto-fix=no", warn]) == 0


def test_main_warning_fails_under_strict_no_autofix(tmp_path):
    warn = write(tmp_path, "warn.bes", task(srd=None))
    assert checker.main(["--strict", "--auto-fix=no", warn]) == 1


def test_main_autofix_returns_one_and_rewrites(tmp_path):
    warn = write(tmp_path, "warn.bes", task(srd=None))
    assert checker.main(["--auto-fix=yes", warn]) == 1
    assert "<SourceReleaseDate>" in (tmp_path / "warn.bes").read_text(encoding="utf-8")


def test_main_disable_suppresses(tmp_path):
    bad = write(tmp_path, "bad.bes", task(mimetype="application/x-python"))
    assert checker.main(["--disable", "E200", bad]) == 0


def test_main_no_files_is_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert checker.main([]) == 0
