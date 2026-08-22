"""
DBIE payload cipher.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
Most DBIE gateway services take their parameters AES-encrypted rather than in
clear text -- `store.encryptPayload(x)` in RBI's Angular bundle. Without
reproducing it you can reach only the handful of services whose parameters are
unencrypted (forex reserves, the session handshake).

This is transport obfuscation, not access control:

  * the parameters are the names of published statistical reports
  * the key material is a constant compiled into a JS bundle that RBI serves
    to every visitor, unauthenticated
  * RBI's own /CIMS_Gateway_LOGIN/.../login_getSapToken hands out key material
    to anyone who asks, with no credential

So reproducing it is a request-formatting step, not a bypass of any protection.
The data reached this way is public government statistics. It stays subject to
the usual caution in CLAUDE.md: internal research use is standard practice,
anything client-facing needs review.

THE SCHEME (lifted verbatim from the bundle's encrypt pipe)
-----------------------------------------------------------
    keySize       = 256
    token         = "48d6b976..."   PBKDF2 password (used as a UTF-8 STRING,
                                    not hex-decoded -- CryptoJS treats a string
                                    password as UTF-8)
    tokenStatus   = "577bd45a..."   PBKDF2 salt, hex-decoded
    tokenResponse = "dc0da04a..."   AES IV, hex-decoded

    key = PBKDF2(token, Hex.parse(tokenStatus), {keySize: 8, iterations: 1000})
    encrypt(e) = AES.encrypt(e, key, {iv: Hex.parse(tokenResponse)})
                    .ciphertext.toString(Base64)

CryptoJS defaults supply the rest and are easy to get wrong:
    * PBKDF2 hasher is SHA1 (not SHA256)
    * keySize is counted in 32-bit WORDS, so 8 words = 256 bits
    * AES defaults to CBC mode with PKCS7 padding
    * .ciphertext excludes any "Salted__" prefix -- raw ciphertext only

SELF-CHECK
----------
The bundle hard-codes "QlAU23oEIEEvtRPWlXajsQ==" as the value meaning null.
Under a correct key that decrypts to the empty string, which is an independent
confirmation of the derivation that needs no network call. verify() asserts it.
"""
from __future__ import annotations

import base64

from Crypto.Cipher import AES
from Crypto.Hash import SHA1
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Util.Padding import pad, unpad

# Defaults compiled into the DBIE bundle. setTokens() can override them at
# runtime from a server response; observed 2026-08-18, it does not.
DEFAULT_TOKEN = "48d6b976d7135745b47b407cd8e659a45d8ebaca4ee95f87d5d939604f472268"
DEFAULT_TOKEN_STATUS = "577bd45a17977269694908d80905c32a"
DEFAULT_TOKEN_RESPONSE = "dc0da04af8fee58593442bf834b30739"

# The bundle's hard-coded "this means null" sentinel.
NULL_SENTINEL = "QlAU23oEIEEvtRPWlXajsQ=="


class DBIECipher:
    """AES-CBC payload cipher matching RBI DBIE's client-side encrypt pipe."""

    def __init__(self, token: str = DEFAULT_TOKEN,
                 token_status: str = DEFAULT_TOKEN_STATUS,
                 token_response: str = DEFAULT_TOKEN_RESPONSE):
        self.iv = bytes.fromhex(token_response)
        self.key = PBKDF2(
            token.encode("utf-8"),
            bytes.fromhex(token_status),
            dkLen=32,
            count=1000,
            hmac_hash_module=SHA1,
        )

    def encrypt(self, value) -> str:
        """Encrypt a scalar to base64 ciphertext. None becomes '' as the app does."""
        text = "" if value is None else str(value)
        cipher = AES.new(self.key, AES.MODE_CBC, iv=self.iv)
        return base64.b64encode(cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))).decode()

    def decrypt(self, blob: str) -> str | None:
        """Decrypt base64 ciphertext. The null sentinel returns None, as in the app."""
        if blob is None or blob == NULL_SENTINEL:
            return None
        cipher = AES.new(self.key, AES.MODE_CBC, iv=self.iv)
        return unpad(cipher.decrypt(base64.b64decode(blob)), AES.block_size).decode("utf-8")

    def verify(self) -> bool:
        """
        Cheap offline correctness check -- no network. Confirms the derivation
        against the bundle's own null sentinel and a round-trip.
        """
        try:
            cipher = AES.new(self.key, AES.MODE_CBC, iv=self.iv)
            sentinel_plain = unpad(
                cipher.decrypt(base64.b64decode(NULL_SENTINEL)), AES.block_size
            ).decode("utf-8")
            probe = "Banking - Performance Indicators"
            return sentinel_plain == "" and self.decrypt(self.encrypt(probe)) == probe
        except (ValueError, UnicodeDecodeError):
            # A wrong key fails padding or UTF-8 decoding. That is a "no",
            # not an exception the caller should have to handle.
            return False


_default: DBIECipher | None = None


def cipher() -> DBIECipher:
    """Process-wide cipher; the key derivation costs 1000 PBKDF2 rounds."""
    global _default
    if _default is None:
        _default = DBIECipher()
    return _default


def encrypt(value) -> str:
    return cipher().encrypt(value)


def decrypt(blob: str) -> str | None:
    return cipher().decrypt(blob)
