"""
Tests for the DBIE payload cipher.

These are offline -- they assert the key derivation against constants lifted
from RBI's own bundle, so they fail loudly if someone "tidies" a parameter
(SHA256 instead of SHA1, keySize in bytes instead of words, ECB instead of CBC)
without noticing that every encrypted request would silently start erroring.
"""
import pytest

from core.rbi_crypto import (
    DEFAULT_TOKEN,
    DEFAULT_TOKEN_STATUS,
    NULL_SENTINEL,
    DBIECipher,
    decrypt,
    encrypt,
)


@pytest.fixture(scope="module")
def c():
    return DBIECipher()


def test_derived_key_matches_bundle_constants(c):
    """Pin the derivation. Any CryptoJS-default mistake changes this value."""
    assert c.key.hex() == "2bdea3a18d23fffa66c2b1909e2a2efb629e40eebd731c5f62b698e7c5a7671e"


def test_null_sentinel_decrypts_to_empty(c):
    """
    The bundle hard-codes NULL_SENTINEL as "this means null". That it decrypts
    cleanly under our key is independent proof of the derivation -- the value
    was produced by RBI's code, not ours.
    """
    cipher_ok = c.verify()
    assert cipher_ok


def test_decrypt_maps_sentinel_to_none(c):
    assert c.decrypt(NULL_SENTINEL) is None


def test_roundtrip(c):
    for probe in ("DBIE", "Indicators", "Daily LAF Operation",
                  "Banking - Performance Indicators"):
        assert c.decrypt(c.encrypt(probe)) == probe


def test_empty_string_encrypts_to_the_null_sentinel(c):
    """
    Falls out of the scheme: the sentinel the bundle calls "null" is simply
    encrypt(""). Another independent check that our key equals RBI's, and the
    reason "" cannot round-trip -- decrypt maps the sentinel to None by design.
    """
    assert c.encrypt("") == NULL_SENTINEL
    assert c.decrypt(c.encrypt("")) is None


def test_encryption_is_deterministic(c):
    """
    Fixed IV means identical input yields identical ciphertext. This is what
    makes a captured payload replayable, and what lets us cache encrypted
    parameters -- assert it rather than assume it.
    """
    assert c.encrypt("Daily LAF Operation") == c.encrypt("Daily LAF Operation")


def test_known_ciphertext(c):
    """A value confirmed accepted by the live gateway."""
    assert c.encrypt("DBIE") == "jw4rTB1+6RG1SG1fTzXNbg=="


def test_none_encrypts_as_empty_string(c):
    """The app coerces null to "" before encrypting; match it."""
    assert c.encrypt(None) == c.encrypt("")


def test_module_level_helpers_share_one_cipher():
    assert decrypt(encrypt("Key Rates")) == "Key Rates"


def test_wrong_key_does_not_validate():
    """Guard against the test above passing for a trivially wrong derivation."""
    bad = DBIECipher(token=DEFAULT_TOKEN, token_status=DEFAULT_TOKEN_STATUS,
                     token_response="00" * 16)
    assert not bad.verify()
