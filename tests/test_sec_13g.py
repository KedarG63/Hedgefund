"""
Offline tests for the Schedule 13G connector and the 13G -> 13D transition.

Parsing is exercised against a cut-down copy of a real Citadel 13G, which is
multi-reporting-person -- the shape that caused fan-out bugs elsewhere.
"""
import pandas as pd
import pytest
from lxml import etree

from connectors.sec_13g import FILING_RULES, FORMS, _norm
from connectors.sec_13d import _num, _txt

SAMPLE = b"""<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g">
  <headerData><submissionType>SCHEDULE 13G</submissionType></headerData>
  <formData>
    <coverPageHeader>
      <securitiesClassTitle>Class A Common Stock</securitiesClassTitle>
      <eventDateRequiresFilingThisStatement>09/16/2025</eventDateRequiresFilingThisStatement>
      <issuerInfo>
        <issuerCIK>0002012345</issuerCIK>
        <issuerName>HCM II Acquisition Corp.</issuerName>
        <issuerCUSIP>40440A101</issuerCUSIP>
      </issuerInfo>
      <designateRulesPursuantThisScheduleFiled>
        <designateRulePursuantThisScheduleFiled>Rule 13d-1(c)</designateRulePursuantThisScheduleFiled>
      </designateRulesPursuantThisScheduleFiled>
    </coverPageHeader>
    <coverPageHeaderReportingPersonDetails>
      <reportingPersonName>Citadel Advisors LLC</reportingPersonName>
      <citizenshipOrOrganization>DE</citizenshipOrOrganization>
      <reportingPersonBeneficiallyOwnedNumberOfShares>
        <soleVotingPower>0.00</soleVotingPower>
        <sharedVotingPower>1012171.00</sharedVotingPower>
        <soleDispositivePower>0.00</soleDispositivePower>
        <sharedDispositivePower>1012171.00</sharedDispositivePower>
      </reportingPersonBeneficiallyOwnedNumberOfShares>
      <reportingPersonBeneficiallyOwnedAggregateNumberOfShares>1012171.00</reportingPersonBeneficiallyOwnedAggregateNumberOfShares>
      <aggregateAmountExcludesCertainSharesFlag>N</aggregateAmountExcludesCertainSharesFlag>
      <classPercent>4.4</classPercent>
      <typeOfReportingPerson>IA</typeOfReportingPerson>
      <typeOfReportingPerson>HC</typeOfReportingPerson>
    </coverPageHeaderReportingPersonDetails>
    <coverPageHeaderReportingPersonDetails>
      <reportingPersonName>Citadel GP LLC</reportingPersonName>
      <classPercent>4.4</classPercent>
      <typeOfReportingPerson>HC</typeOfReportingPerson>
    </coverPageHeaderReportingPersonDetails>
    <items>
      <item1>HCM II Acquisition Corp.</item1>
      <item10>N By signing below I certify that the securities were not acquired
              to influence control.</item10>
    </items>
  </formData>
</edgarSubmission>
"""


@pytest.fixture(scope="module")
def root():
    return etree.fromstring(SAMPLE)


# ------------------------------------------------- schema differs from 13D
def test_13g_uses_its_own_element_names(root):
    """
    13G is NOT 13D with a different name. Wrong element names parse to silence,
    not an error, so these are pinned:
        percentOfClass (13D)  vs  classPercent (13G)
        reportingPersonInfo   vs  coverPageHeaderReportingPersonDetails
    """
    people = root.findall("{*}formData/{*}coverPageHeaderReportingPersonDetails")
    assert len(people) == 2
    assert _num(people[0].find("{*}classPercent")) == 4.4
    # the 13D spellings must find nothing here
    assert root.findall("{*}formData/{*}reportingPersons/{*}reportingPersonInfo") == []
    assert people[0].find("{*}percentOfClass") is None


def test_lowercase_namespace_is_handled(root):
    """13D is .../schedule13D, 13G is .../schedule13g. Wildcards absorb that."""
    assert root.nsmap[None].endswith("schedule13g")
    assert _txt(root.find("{*}headerData/{*}submissionType")) == "SCHEDULE 13G"


def test_all_reporting_persons_are_found(root):
    names = [_txt(p.find("{*}reportingPersonName"))
             for p in root.findall("{*}formData/{*}coverPageHeaderReportingPersonDetails")]
    assert names == ["Citadel Advisors LLC", "Citadel GP LLC"]


def test_voting_powers_parse(root):
    p = root.find("{*}formData/{*}coverPageHeaderReportingPersonDetails")
    owned = p.find("{*}reportingPersonBeneficiallyOwnedNumberOfShares")
    assert _num(owned.find("{*}sharedVotingPower")) == 1012171.0
    assert _num(owned.find("{*}soleVotingPower")) == 0.0
    assert _num(p.find("{*}reportingPersonBeneficiallyOwnedAggregateNumberOfShares")) == 1012171.0


def test_filing_rule_is_extracted(root):
    rule = _txt(root.find(
        "{*}formData/{*}coverPageHeader/{*}designateRulesPursuantThisScheduleFiled/"
        "{*}designateRulePursuantThisScheduleFiled"))
    assert rule == "Rule 13d-1(c)"
    assert FILING_RULES[rule] == "passive investor"


def test_all_three_filing_rules_are_known():
    assert set(FILING_RULES) == {"Rule 13d-1(b)", "Rule 13d-1(c)", "Rule 13d-1(d)"}


def test_forms_cover_originals_and_amendments():
    assert set(FORMS) == {"SCHEDULE 13G", "SCHEDULE 13G/A"}


# ------------------------------------------------------------- name matching
@pytest.mark.parametrize("a,b", [
    ("Citadel Advisors LLC", "CITADEL ADVISORS LLC"),
    ("Elliott Investment Management L.P.", "ELLIOTT INVESTMENT MANAGEMENT LP"),
    ("Vanguard Group Inc", "VANGUARD GROUP, INC."),
])
def test_norm_matches_the_same_filer_across_forms(a, b):
    """13G and 13D spell the same filer differently; the key must bridge that."""
    assert _norm(pd.Series([a])).iloc[0] == _norm(pd.Series([b])).iloc[0]


def test_norm_keeps_distinct_filers_apart():
    names = ["Citadel Advisors LLC", "Elliott Investment Management LP",
             "Vanguard Group Inc", "BlackRock Inc"]
    keys = _norm(pd.Series(names)).tolist()
    assert len(set(keys)) == len(names)


def test_norm_handles_nulls():
    assert _norm(pd.Series([None, ""])).tolist() == ["", ""]


# ---------------------------------------------------------------- transitions
def test_transition_requires_13d_to_come_after_13g():
    """
    Only 13G-then-13D is an escalation. The reverse (an activist standing down
    to passive) is a different event and must not be reported as a switch.
    """
    g = pd.DataFrame([{"issuer_cik": "1", "person_name": "X LP", "first_13g": "2024-01-01"}])
    d = pd.DataFrame([{"issuer_cik": "1", "person_name": "X LP", "first_13d": "2023-01-01"}])
    g["key"] = _norm(g["person_name"])
    d["key"] = _norm(d["person_name"])
    merged = g.merge(d, on=["issuer_cik", "key"])
    assert merged[merged["first_13d"] > merged["first_13g"]].empty


def test_transition_detected_when_13d_follows_13g():
    g = pd.DataFrame([{"issuer_cik": "1", "person_name": "X LP", "first_13g": "2023-01-01"}])
    d = pd.DataFrame([{"issuer_cik": "1", "person_name": "X, L.P.", "first_13d": "2025-06-01"}])
    g["key"] = _norm(g["person_name"])
    d["key"] = _norm(d["person_name"])
    merged = g.merge(d, on=["issuer_cik", "key"])
    out = merged[merged["first_13d"] > merged["first_13g"]]
    assert len(out) == 1


def test_transition_requires_the_same_issuer():
    """A filer switching to activism elsewhere says nothing about this issuer."""
    g = pd.DataFrame([{"issuer_cik": "1", "person_name": "X LP", "first_13g": "2023-01-01"}])
    d = pd.DataFrame([{"issuer_cik": "2", "person_name": "X LP", "first_13d": "2025-06-01"}])
    g["key"] = _norm(g["person_name"])
    d["key"] = _norm(d["person_name"])
    assert g.merge(d, on=["issuer_cik", "key"]).empty


# ------------------------------------------- issuer element capitalisation
def test_issuer_cik_uses_13g_capitalisation():
    """
    13G writes <issuerCik>/<issuerCusip>; 13D writes <issuerCIK>/<issuerCUSIP>.
    XML element names are case-sensitive, so the 13D spelling returns None here
    rather than raising. That made every 13G issuer_cik NULL, which in turn made
    the 13G->13D join match nothing and report zero transitions -- a signal that
    silently did not exist.
    """
    from connectors.sec_13g import _first

    real = etree.fromstring(b"""<?xml version="1.0"?>
    <edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g">
      <formData><coverPageHeader><issuerInfo>
        <issuerCik>0001023313</issuerCik>
        <issuerName>Forrester Research, Inc.</issuerName>
        <issuerCusip>346563109</issuerCusip>
      </issuerInfo></coverPageHeader></formData>
    </edgarSubmission>""")
    issuer = real.find("{*}formData/{*}coverPageHeader/{*}issuerInfo")

    # the 13D spelling alone finds nothing
    assert issuer.find("{*}issuerCIK") is None
    # _first tries both
    assert _txt(_first(issuer, "issuerCik", "issuerCIK")) == "0001023313"
    assert _txt(_first(issuer, "issuerCusip", "issuerCUSIP")) == "346563109"


def test_first_accepts_either_spelling():
    from connectors.sec_13g import _first
    upper = etree.fromstring(b"<a><issuerCIK>123</issuerCIK></a>")
    lower = etree.fromstring(b"<a><issuerCik>123</issuerCik></a>")
    for node in (upper, lower):
        assert _txt(_first(node, "issuerCik", "issuerCIK")) == "123"


def test_first_returns_none_for_missing_and_null_node():
    from connectors.sec_13g import _first
    assert _first(None, "anything") is None
    assert _first(etree.fromstring(b"<a/>"), "missing") is None
