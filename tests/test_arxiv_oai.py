"""Tests for `scripts.arxiv_oai` — OAI-PMH parsing and on-disk cache replay.

Covers ``cache_filename``, ``parse_record`` (including the structured-author
change vs. the upstream collapse), and ``iter_cached_records``. HTTP-layer
tests (retry, ``Retry-After``, resumption-token paging across real GET
calls) are deferred to slice 2 of the Phase 3 plan, when we have an end-to-
end ingest CLI to smoke against the live OAI endpoint without pulling in
``respx`` as a test dependency.
"""

import pathlib
import sys
import xml.etree.ElementTree as ET

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import arxiv_oai  # noqa: E402
from arxiv_oai import (  # noqa: E402
    cache_filename,
    iter_cached_records,
    parse_record,
)


def _wrap(body: str) -> str:
    """Wrap an XML body in the OAI-PMH envelope."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2024-01-22T00:00:00Z</responseDate>
  <request verb="ListRecords" metadataPrefix="arXiv" from="2024-01-01">https://oaipmh.arxiv.org/oai</request>
  {body}
</OAI-PMH>"""


def _record(
    arxiv_id: str = "2401.12345",
    datestamp: str = "2024-01-22",
    title: str = "Test Paper",
    abstract: str = "Abstract body.",
    categories: str = "cs.CL cs.LG",
    created: str = "2024-01-22",
    updated: str | None = "2024-01-25",
    deleted: bool = False,
    authors_xml: str | None = None,
) -> str:
    if deleted:
        return f"""
    <record>
      <header status="deleted">
        <identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>{datestamp}</datestamp>
      </header>
    </record>"""
    updated_el = f"<updated>{updated}</updated>" if updated else ""
    if authors_xml is None:
        authors_xml = (
            "<authors>"
            "<author><keyname>Smith</keyname><forenames>Alice</forenames></author>"
            "<author><keyname>Jones</keyname><forenames>Bob C.</forenames></author>"
            "</authors>"
        )
    return f"""
    <record>
      <header>
        <identifier>oai:arXiv.org:{arxiv_id}</identifier>
        <datestamp>{datestamp}</datestamp>
        <setSpec>cs</setSpec>
      </header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>{arxiv_id}</id>
          <created>{created}</created>
          {updated_el}
          {authors_xml}
          <title>{title}</title>
          <categories>{categories}</categories>
          <comments>9 pages</comments>
          <abstract>{abstract}</abstract>
        </arXiv>
      </metadata>
    </record>"""


def _list_records_page(records_xml: str, resumption_token: str | None = None) -> str:
    token_el = (
        f"<resumptionToken>{resumption_token}</resumptionToken>" if resumption_token else ""
    )
    return _wrap(f"<ListRecords>{records_xml}{token_el}</ListRecords>")


def _record_element(record_xml: str) -> ET.Element:
    """Parse one record XML string into its ``<record>`` Element."""
    root = ET.fromstring(_list_records_page(record_xml))
    found = root.find(
        "{http://www.openarchives.org/OAI/2.0/}ListRecords/{http://www.openarchives.org/OAI/2.0/}record"
    )
    assert found is not None, "test fixture failed to produce a <record>"
    return found


class TestCacheFilename:
    def test_stable_across_param_order(self) -> None:
        a = {"verb": "ListRecords", "metadataPrefix": "arXiv", "from": "2024-01-01"}
        b = {"from": "2024-01-01", "metadataPrefix": "arXiv", "verb": "ListRecords"}
        assert cache_filename(a) == cache_filename(b)

    def test_different_params_different_name(self) -> None:
        a = {"verb": "ListRecords", "from": "2024-01-01"}
        b = {"verb": "ListRecords", "from": "2024-01-02"}
        assert cache_filename(a) != cache_filename(b)

    def test_ends_in_xml(self) -> None:
        assert cache_filename({"verb": "ListRecords"}).endswith(".xml")


class TestParseRecord:
    def test_parses_basic_fields(self) -> None:
        parsed = parse_record(_record_element(_record()))
        assert parsed["id"] == "2401.12345"
        assert parsed["title"] == "Test Paper"
        assert parsed["abstract"] == "Abstract body."
        assert parsed["categories"] == "cs.CL cs.LG"
        assert parsed["primary_category"] == "cs.CL"
        assert parsed["submitted_date"] == "2024-01-22"
        assert parsed["updated_date"] == "2024-01-25"
        assert parsed["oai_datestamp"] == "2024-01-22"

    def test_returns_none_for_deleted(self) -> None:
        assert parse_record(_record_element(_record(deleted=True))) is None

    def test_collapses_whitespace_in_title_and_abstract(self) -> None:
        body = _record(title="  Many   spaces\n  here  ", abstract="Multi\n  line\n abstract  text")
        parsed = parse_record(_record_element(body))
        assert parsed["title"] == "Many spaces here"
        assert parsed["abstract"] == "Multi line abstract text"

    def test_missing_updated_becomes_none(self) -> None:
        parsed = parse_record(_record_element(_record(updated=None)))
        assert parsed["updated_date"] is None

    def test_empty_id_returns_none(self) -> None:
        # arxiv id is the primary key; a record without one would either
        # crash the INSERT in slice 2 or collide on empty-string PK.
        parsed = parse_record(_record_element(_record(arxiv_id="")))
        assert parsed is None

    def test_empty_datestamp_returns_none(self) -> None:
        # Without a datestamp, slice 2's incremental skip can't detect
        # "this paper changed" — drop the record at parse time.
        parsed = parse_record(_record_element(_record(datestamp="")))
        assert parsed is None


class TestStructuredAuthors:
    """The Phase 3 carry-over: WORK.md section 2.1 — keep <keyname>/<forenames>/<affiliation> separate."""

    def test_authors_are_dicts_not_strings(self) -> None:
        parsed = parse_record(_record_element(_record()))
        authors = parsed["authors"]
        assert isinstance(authors, list)
        assert len(authors) == 2
        for a in authors:
            assert isinstance(a, dict)
            assert {"keyname", "forenames", "affiliation", "display_name"} <= set(a.keys())

    def test_keyname_and_forenames_preserved_separately(self) -> None:
        parsed = parse_record(_record_element(_record()))
        assert parsed["authors"][0]["keyname"] == "Smith"
        assert parsed["authors"][0]["forenames"] == "Alice"
        assert parsed["authors"][1]["keyname"] == "Jones"
        assert parsed["authors"][1]["forenames"] == "Bob C."

    def test_display_name_is_forenames_then_keyname(self) -> None:
        parsed = parse_record(_record_element(_record()))
        display = [a["display_name"] for a in parsed["authors"]]
        assert display == ["Alice Smith", "Bob C. Jones"]

    def test_affiliation_captured_when_present(self) -> None:
        body = _record(
            authors_xml=(
                "<authors>"
                "<author>"
                "<keyname>Smith</keyname>"
                "<forenames>Alice</forenames>"
                "<affiliation>MIT</affiliation>"
                "</author>"
                "</authors>"
            )
        )
        parsed = parse_record(_record_element(body))
        assert parsed["authors"][0]["affiliation"] == "MIT"

    def test_affiliation_none_when_absent(self) -> None:
        parsed = parse_record(_record_element(_record()))
        assert parsed["authors"][0]["affiliation"] is None

    def test_suffix_folded_into_display_name(self) -> None:
        body = _record(
            authors_xml=(
                "<authors>"
                "<author>"
                "<keyname>Smith</keyname>"
                "<forenames>Alice</forenames>"
                "<suffix>Jr.</suffix>"
                "</author>"
                "</authors>"
            )
        )
        parsed = parse_record(_record_element(body))
        assert parsed["authors"][0]["display_name"] == "Alice Smith Jr."
        # Structured fields don't carry suffix today; it survives via display_name only.
        assert "suffix" not in parsed["authors"][0]

    def test_keyname_only_works(self) -> None:
        body = _record(
            authors_xml="<authors><author><keyname>Mononym</keyname></author></authors>"
        )
        parsed = parse_record(_record_element(body))
        assert parsed["authors"][0]["keyname"] == "Mononym"
        assert parsed["authors"][0]["forenames"] == ""
        assert parsed["authors"][0]["display_name"] == "Mononym"

    def test_empty_author_element_skipped(self) -> None:
        # No name parts AND no affiliation → junk record, drop.
        body = _record(authors_xml="<authors><author></author></authors>")
        parsed = parse_record(_record_element(body))
        assert parsed["authors"] == []

    def test_author_with_only_affiliation_kept(self) -> None:
        # Vanishingly rare in real arxiv data but documented as the explicit
        # policy: affiliation alone is enough to keep the record.
        body = _record(
            authors_xml=(
                "<authors>"
                "<author><affiliation>CERN</affiliation></author>"
                "</authors>"
            )
        )
        parsed = parse_record(_record_element(body))
        assert parsed["authors"] == [
            {
                "keyname": "",
                "forenames": "",
                "affiliation": "CERN",
                "display_name": "",
            }
        ]

    def test_no_authors_element_returns_empty_list(self) -> None:
        # Author parsing pulls from <authors>; an arXiv record without one
        # (rare but possible) should yield [] rather than crash.
        body = f"""
        <record>
          <header>
            <identifier>oai:arXiv.org:2401.99999</identifier>
            <datestamp>2024-01-22</datestamp>
          </header>
          <metadata>
            <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
              <id>2401.99999</id>
              <title>No Authors</title>
              <abstract>x</abstract>
              <categories>cs.CL</categories>
              <created>2024-01-22</created>
            </arXiv>
          </metadata>
        </record>"""
        parsed = parse_record(_record_element(body))
        assert parsed["authors"] == []


class TestIterCachedRecords:
    def test_walks_xml_files_in_sorted_order(self, tmp_path: pathlib.Path) -> None:
        # Write files in reverse name order to confirm the iterator sorts by
        # filename, not by filesystem creation/inode order. With inode-order
        # iteration this test would return [0002, 0001] and fail.
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "02.xml").write_text(
            _list_records_page(_record(arxiv_id="2401.0002")), encoding="utf-8"
        )
        (cache / "01.xml").write_text(
            _list_records_page(_record(arxiv_id="2401.0001")), encoding="utf-8"
        )
        records = list(iter_cached_records(cache))
        assert [r["id"] for r in records] == ["2401.0001", "2401.0002"]

    def test_skips_files_without_list_records(self, tmp_path: pathlib.Path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "01.xml").write_text(_wrap("<Identify/>"), encoding="utf-8")
        (cache / "02.xml").write_text(
            _list_records_page(_record(arxiv_id="2401.0001")), encoding="utf-8"
        )
        records = list(iter_cached_records(cache))
        assert [r["id"] for r in records] == ["2401.0001"]

    def test_deleted_records_filtered_out(self, tmp_path: pathlib.Path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "01.xml").write_text(
            _list_records_page(
                _record(arxiv_id="2401.0001")
                + _record(arxiv_id="2401.0002", deleted=True)
            ),
            encoding="utf-8",
        )
        records = list(iter_cached_records(cache))
        assert [r["id"] for r in records] == ["2401.0001"]


class TestModuleConfig:
    def test_user_agent_includes_mailto(self) -> None:
        assert "mailto:" in arxiv_oai.USER_AGENT

    def test_oai_endpoint_is_arxiv(self) -> None:
        assert "arxiv.org" in arxiv_oai.OAI_ENDPOINT
