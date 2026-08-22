"""
Offline tests for the Schedule 13D connector.

Parsing is exercised against a cut-down but structurally faithful copy of a real
filing (the GAMCO/Tredegar 13D/A), so the multi-reporting-person shape -- the
thing that caused fan-out bugs in the 13F and insider datasets -- is covered.
"""
import pytest
from lxml import etree

from connectors.sec_13d import (
    FORMS,
    PURPOSE_PATTERNS,
    classify_purpose,
    latest_quarter,
    _num,
    _txt,
)

SAMPLE = b"""<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13D">
  <headerData>
    <submissionType>SCHEDULE 13D/A</submissionType>
    <previousAccessionNumber>0000807249-07-000385</previousAccessionNumber>
  </headerData>
  <formData>
    <coverPageHeader>
      <amendmentNo>32</amendmentNo>
      <securitiesClassTitle>Common Stock</securitiesClassTitle>
      <dateOfEvent>09/29/2025</dateOfEvent>
      <issuerInfo>
        <issuerCIK>0000850429</issuerCIK>
        <issuerCUSIP>894650100</issuerCUSIP>
        <issuerName>TREDEGAR CORP</issuerName>
      </issuerInfo>
    </coverPageHeader>
    <reportingPersons>
      <reportingPersonInfo>
        <reportingPersonCIK>0000807249</reportingPersonCIK>
        <reportingPersonName>GAMCO INVESTORS, INC. ET AL</reportingPersonName>
        <soleVotingPower>0.00</soleVotingPower>
        <aggregateAmountOwned>0.00</aggregateAmountOwned>
        <percentOfClass>0.0</percentOfClass>
        <typeOfReportingPerson>CO</typeOfReportingPerson>
        <typeOfReportingPerson>HC</typeOfReportingPerson>
      </reportingPersonInfo>
      <reportingPersonInfo>
        <reportingPersonCIK>0001081407</reportingPersonCIK>
        <reportingPersonName>GABELLI FUNDS LLC</reportingPersonName>
        <soleVotingPower>1151108.00</soleVotingPower>
        <aggregateAmountOwned>1151108.00</aggregateAmountOwned>
        <percentOfClass>3.3</percentOfClass>
        <typeOfReportingPerson>IA</typeOfReportingPerson>
      </reportingPersonInfo>
    </reportingPersons>
    <items1To7>
      <item4>The Reporting Persons intend to nominate director candidates and
              engage in discussions with management regarding strategic
              alternatives.</item4>
      <item5>The aggregate number of Securities is 7,430,113 shares,
             representing 21.29%.</item5>
    </items1To7>
  </formData>
</edgarSubmission>
"""


@pytest.fixture(scope="module")
def root():
    return etree.fromstring(SAMPLE)


# ------------------------------------------------------------------ structure
def test_wildcard_namespace_finds_elements(root):
    """
    The schema uses a DEFAULT namespace here, but other filers use a prefixed
    one. Wildcard matching must work regardless -- keying off nsmap[None] parsed
    zero rows for prefixed filings in the 13F and N-PORT parsers.
    """
    assert _txt(root.find("{*}headerData/{*}submissionType")) == "SCHEDULE 13D/A"
    issuer = root.find("{*}formData/{*}coverPageHeader/{*}issuerInfo")
    assert _txt(issuer.find("{*}issuerName")) == "TREDEGAR CORP"
    assert _txt(issuer.find("{*}issuerCUSIP")) == "894650100"


def test_all_reporting_persons_are_found(root):
    """A 13D routinely carries many filers; none may be dropped."""
    people = root.findall("{*}formData/{*}reportingPersons/{*}reportingPersonInfo")
    assert len(people) == 2
    names = [_txt(p.find("{*}reportingPersonName")) for p in people]
    assert "GABELLI FUNDS LLC" in names


def test_numbers_parse_with_separators(root):
    p = root.findall("{*}formData/{*}reportingPersons/{*}reportingPersonInfo")[1]
    assert _num(p.find("{*}soleVotingPower")) == 1151108.0
    assert _num(p.find("{*}percentOfClass")) == 3.3


def test_num_returns_none_for_missing_or_unparseable(root):
    assert _num(None) is None
    assert _num(root.find("{*}formData/{*}coverPageHeader/{*}securitiesClassTitle")) is None


def test_multiple_person_types_are_preserved(root):
    p = root.findall("{*}formData/{*}reportingPersons/{*}reportingPersonInfo")[0]
    types = [_txt(x) for x in p.findall("{*}typeOfReportingPerson")]
    assert types == ["CO", "HC"]


def test_item_text_is_whitespace_normalised(root):
    item4 = _txt(root.find("{*}formData/{*}items1To7/{*}item4"))
    assert "\n" not in item4
    assert "  " not in item4


# ------------------------------------------------------------------- purpose
def test_classify_purpose_on_real_text(root):
    flags = classify_purpose(_txt(root.find("{*}formData/{*}items1To7/{*}item4")))
    assert flags["board_representation"]
    assert flags["engage_management"]
    assert flags["seeks_sale_or_merger"]      # "strategic alternatives"
    assert not flags["capital_return"]


def test_classify_purpose_handles_missing_item4():
    """
    Amendments often incorporate Item 4 by reference, leaving it empty. That
    must yield all-False rather than raising -- and callers should read
    item4_purpose being null as "unknown", not "passive".
    """
    flags = classify_purpose(None)
    assert set(flags) == set(PURPOSE_PATTERNS)
    assert not any(flags.values())
    assert not any(classify_purpose("").values())


@pytest.mark.parametrize("text,expected", [
    ("intends to explore a sale of the company", "seeks_sale_or_merger"),
    ("will nominate director candidates at the annual meeting", "board_representation"),
    ("intends to vote against the proposed transaction as inadequate", "opposes_transaction"),
    ("urges a share repurchase programme", "capital_return"),
    ("seeks to acquire control of the issuer", "control_intent"),
])
def test_each_pattern_fires(text, expected):
    assert classify_purpose(text)[expected]


def test_passive_language_does_not_trip_activist_flags():
    flags = classify_purpose("The Reporting Person holds the shares for investment "
                             "purposes only and has no plans or proposals.")
    assert not flags["board_representation"]
    assert not flags["control_intent"]
    assert not flags["seeks_sale_or_merger"]


# ------------------------------------------------------------------ plumbing
def test_forms_cover_originals_and_amendments():
    assert "SCHEDULE 13D" in FORMS and "SCHEDULE 13D/A" in FORMS


def test_discover_deduplicates_multi_cik_index_entries(monkeypatch):
    """
    master.idx indexes a filing once per ASSOCIATED CIK, and a 13D is associated
    with both the reporting person and the subject issuer. Half the 2025Q3 index
    rows were the same 1,393 documents listed twice -- which meant fetching every
    document twice and storing every reporting person twice.
    """
    import pandas as pd
    from connectors import sec_13d

    fake = pd.DataFrame([
        # one filing, indexed under the filer AND the issuer
        {"cik": 1607203, "entity_idx": "Eagle Point Credit Management LLC",
         "form": "SCHEDULE 13D", "filed": "2025-07-08", "accn": "0001104659-25-066477"},
        {"cik": 2013536, "entity_idx": "Eagle Point Defensive Income Trust",
         "form": "SCHEDULE 13D", "filed": "2025-07-08", "accn": "0001104659-25-066477"},
        # a genuinely separate filing
        {"cik": 999, "entity_idx": "OTHER CORP",
         "form": "SCHEDULE 13D/A", "filed": "2025-07-09", "accn": "0000000-25-000001"},
        # a form we do not want
        {"cik": 999, "entity_idx": "OTHER CORP",
         "form": "SCHEDULE 13G", "filed": "2025-07-09", "accn": "0000000-25-000002"},
    ])
    monkeypatch.setattr(sec_13d, "filing_index", lambda y, q: fake)

    out = sec_13d.discover(2025, 3)
    assert len(out) == 2, "the duplicated accession must collapse to one"
    assert out["accn"].is_unique
    assert set(out["form"]) == {"SCHEDULE 13D", "SCHEDULE 13D/A"}


def test_discover_universe_filter_matches_either_associated_cik(monkeypatch):
    """
    Either the reporting person or the issuer may be the CIK of interest, so the
    universe filter must run BEFORE dedup or a filing is lost depending on which
    row happened to survive.
    """
    import pandas as pd
    from connectors import sec_13d

    fake = pd.DataFrame([
        {"cik": 111, "entity_idx": "ACTIVIST LP", "form": "SCHEDULE 13D",
         "filed": "2025-07-08", "accn": "A-1"},
        {"cik": 222, "entity_idx": "TARGET INC", "form": "SCHEDULE 13D",
         "filed": "2025-07-08", "accn": "A-1"},
    ])
    monkeypatch.setattr(sec_13d, "filing_index", lambda y, q: fake)

    # asking by the ISSUER's cik must still find the filing
    assert len(sec_13d.discover(2025, 3, ciks={222})) == 1
    # and so must asking by the activist's
    assert len(sec_13d.discover(2025, 3, ciks={111})) == 1
    assert len(sec_13d.discover(2025, 3, ciks={333})) == 0


@pytest.mark.parametrize("today,expected", [
    ((2026, 8, 22), (2026, 2)),
    ((2026, 1, 9),  (2025, 4)),
])
def test_latest_quarter(today, expected):
    from datetime import date
    assert latest_quarter(date(*today)) == expected
