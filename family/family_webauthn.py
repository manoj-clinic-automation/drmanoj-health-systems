#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
family_webauthn.py -- passkeys (Face ID / Touch ID) for the Family Edition, verified here.

FAMILY_EDITION_V1. The server's Python has no `cryptography` package and the
project installs nothing new, so the few pieces WebAuthn needs are written out
in full and kept small enough to read:

  * a CBOR reader for the subset authenticators send (maps, arrays, integers,
    byte and text strings, booleans, null);
  * COSE keys: EC2 P-256 (alg -7, ES256 -- every Apple and Android passkey) and
    RSA (alg -257, RS256 -- some Windows Hello keys);
  * ES256 verification (ECDSA over P-256, DER signature) and RS256 verification
    (PKCS#1 v1.5 with SHA-256);
  * registration ("none" attestation) and assertion checks.

What is checked, in both ceremonies: the client data's type, the challenge
(issued by this server, single use, 3 minutes), the ORIGIN, the relying-party
ID hash in the authenticator data, user presence AND user verification (Face
ID / Touch ID / device PIN -- a passkey that did not verify the user is
refused), and on sign-in the signature over authenticatorData || SHA-256
(clientDataJSON) with the stored public key, plus the signature counter.
Only verification lives here -- no private key is ever made or held.

test_family_webauthn_crypto.py (offline) checks ES256 and RS256 against the
`cryptography` package on thousands of random keys, messages and tampered
signatures. Python 3.9.
"""
import base64
import hashlib
import json
import struct

MARKER = "FAMILY_EDITION_V1"


class WebAuthnError(Exception):
    pass


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def unb64u(s):
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ------------------------------------------------------------------ CBOR
def cbor_decode(data, pos=0):
    """(value, next position). The subset WebAuthn uses."""
    if pos >= len(data):
        raise WebAuthnError("CBOR: truncated")
    ib = data[pos]
    major, info = ib >> 5, ib & 0x1F
    pos += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        n = {24: 1, 25: 2, 26: 4, 27: 8}[info]
        if pos + n > len(data):
            raise WebAuthnError("CBOR: truncated")
        arg = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    else:
        raise WebAuthnError("CBOR: indefinite lengths are not accepted")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        if pos + arg > len(data):
            raise WebAuthnError("CBOR: truncated")
        raw = data[pos:pos + arg]
        return (bytes(raw) if major == 2 else raw.decode("utf-8")), pos + arg
    if major == 4:
        out = []
        for _ in range(arg):
            v, pos = cbor_decode(data, pos)
            out.append(v)
        return out, pos
    if major == 5:
        out = {}
        for _ in range(arg):
            k, pos = cbor_decode(data, pos)
            v, pos = cbor_decode(data, pos)
            out[k] = v
        return out, pos
    if major == 7:
        simple = {20: False, 21: True, 22: None}
        if info in simple:
            return simple[info], pos
    raise WebAuthnError("CBOR: unsupported item")


# ------------------------------------------------------------------ P-256
P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def on_curve(pt):
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def _jac_double(p):
    X, Y, Z = p
    if Y == 0:
        return (0, 1, 0)
    ysq = Y * Y % P
    S = 4 * X * ysq % P
    M = (3 * X * X + A * pow(Z, 4, P)) % P
    nx = (M * M - 2 * S) % P
    ny = (M * (S - nx) - 8 * ysq * ysq) % P
    nz = 2 * Y * Z % P
    return (nx, ny, nz)


def _jac_add(p, q):
    if p[2] == 0:
        return q
    if q[2] == 0:
        return p
    U1 = p[0] * q[2] * q[2] % P
    U2 = q[0] * p[2] * p[2] % P
    S1 = p[1] * pow(q[2], 3, P) % P
    S2 = q[1] * pow(p[2], 3, P) % P
    if U1 == U2:
        return _jac_double(p) if S1 == S2 else (0, 1, 0)
    H = (U2 - U1) % P
    R = (S2 - S1) % P
    H2 = H * H % P
    H3 = H * H2 % P
    U1H2 = U1 * H2 % P
    nx = (R * R - H3 - 2 * U1H2) % P
    ny = (R * (U1H2 - nx) - S1 * H3) % P
    nz = H * p[2] * q[2] % P
    return (nx, ny, nz)


def _jac_mul(pt, k):
    r = (0, 1, 0)
    q = (pt[0], pt[1], 1)
    while k:
        if k & 1:
            r = _jac_add(r, q)
        q = _jac_double(q)
        k >>= 1
    return r


def _to_affine(p):
    if p[2] == 0:
        return None
    zi = pow(p[2], P - 2, P)
    return (p[0] * zi * zi % P, p[1] * pow(zi, 3, P) % P)


def der_sig(sig):
    """(r, s) from a DER ECDSA-Sig-Value, strictly."""
    if len(sig) < 8 or sig[0] != 0x30 or sig[1] != len(sig) - 2:
        raise WebAuthnError("signature is not DER")
    pos, out = 2, []
    for _ in range(2):
        if sig[pos] != 0x02:
            raise WebAuthnError("signature is not DER")
        ln = sig[pos + 1]
        v = sig[pos + 2:pos + 2 + ln]
        if len(v) != ln or ln == 0 or (v[0] & 0x80):
            raise WebAuthnError("signature is not DER")
        out.append(int.from_bytes(v, "big"))
        pos += 2 + ln
    if pos != len(sig):
        raise WebAuthnError("signature is not DER")
    return out[0], out[1]


def es256_verify(x, y, msg, sig):
    pt = (x, y)
    if not on_curve(pt):
        return False
    try:
        r, s = der_sig(sig)
    except WebAuthnError:
        return False
    if not (1 <= r < N and 1 <= s < N):
        return False
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, N - 2, N)
    u1, u2 = e * w % N, r * w % N
    pnt = _to_affine(_jac_add(_jac_mul(G, u1), _jac_mul(pt, u2)))
    return pnt is not None and pnt[0] % N == r


_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def rs256_verify(n, e, msg, sig):
    k = (n.bit_length() + 7) // 8
    if len(sig) != k or n.bit_length() < 2048:
        return False
    m = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    want = b"\x00\x01" + b"\xff" * (k - 3 - len(_SHA256_PREFIX) - 32) + b"\x00" + \
        _SHA256_PREFIX + hashlib.sha256(msg).digest()
    return m == want


# ------------------------------------------------------------------ COSE
def cose_key(cbor_map):
    """A stored key: {"alg": -7, "x": .., "y": ..} or {"alg": -257, "n": .., "e": ..}."""
    kty, alg = cbor_map.get(1), cbor_map.get(3)
    if kty == 2 and alg == -7 and cbor_map.get(-1) == 1:
        x = int.from_bytes(cbor_map.get(-2) or b"", "big")
        y = int.from_bytes(cbor_map.get(-3) or b"", "big")
        if not on_curve((x, y)):
            raise WebAuthnError("the key is not on P-256")
        return {"alg": -7, "x": "%x" % x, "y": "%x" % y}
    if kty == 3 and alg == -257:
        n = int.from_bytes(cbor_map.get(-1) or b"", "big")
        e = int.from_bytes(cbor_map.get(-2) or b"", "big")
        if n.bit_length() < 2048 or e < 3:
            raise WebAuthnError("the RSA key is too weak")
        return {"alg": -257, "n": "%x" % n, "e": "%x" % e}
    raise WebAuthnError("unsupported key type (only ES256 and RS256)")


def verify_sig(key, msg, sig):
    if key.get("alg") == -7:
        return es256_verify(int(key["x"], 16), int(key["y"], 16), msg, sig)
    if key.get("alg") == -257:
        return rs256_verify(int(key["n"], 16), int(key["e"], 16), msg, sig)
    return False


# ------------------------------------------------------------------ ceremonies
FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40


def parse_auth_data(ad):
    if len(ad) < 37:
        raise WebAuthnError("authenticator data too short")
    out = {"rp_hash": ad[:32], "flags": ad[32], "count": struct.unpack(">I", ad[33:37])[0]}
    if out["flags"] & FLAG_AT:
        if len(ad) < 55:
            raise WebAuthnError("attested credential data too short")
        ln = struct.unpack(">H", ad[53:55])[0]
        out["cred_id"] = ad[55:55 + ln]
        out["cose"], _end = cbor_decode(ad, 55 + ln)
    return out


def _client_data(raw, kind, challenge, origin):
    try:
        cd = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise WebAuthnError("client data is not JSON")
    if cd.get("type") != kind:
        raise WebAuthnError("wrong ceremony type")
    if cd.get("challenge") != challenge:
        raise WebAuthnError("challenge does not match")
    if cd.get("origin") != origin:
        raise WebAuthnError("origin does not match")
    return cd


def _flags(ad, rp_id):
    if ad["rp_hash"] != hashlib.sha256(rp_id.encode("ascii")).digest():
        raise WebAuthnError("relying party does not match")
    if not ad["flags"] & FLAG_UP:
        raise WebAuthnError("user presence missing")
    if not ad["flags"] & FLAG_UV:
        raise WebAuthnError("user verification (Face ID / Touch ID) missing")


def verify_registration(cred, challenge, origin, rp_id):
    """-> {"cred_id": b64u, "key": {...}, "count": int}. Raises WebAuthnError."""
    resp = cred.get("response") or {}
    _client_data(unb64u(resp.get("clientDataJSON")), "webauthn.create", challenge, origin)
    att, _end = cbor_decode(unb64u(resp.get("attestationObject")))
    if not isinstance(att, dict) or att.get("fmt") != "none":
        raise WebAuthnError("only 'none' attestation is accepted")
    ad = parse_auth_data(att.get("authData") or b"")
    _flags(ad, rp_id)
    if "cred_id" not in ad:
        raise WebAuthnError("no credential in the attestation")
    if b64u(ad["cred_id"]) != cred.get("id"):
        raise WebAuthnError("credential id does not match")
    return {"cred_id": b64u(ad["cred_id"]), "key": cose_key(ad["cose"]), "count": ad["count"]}


def verify_assertion(cred, challenge, origin, rp_id, key, stored_count):
    """-> the new signature counter. Raises WebAuthnError."""
    resp = cred.get("response") or {}
    raw_cd = unb64u(resp.get("clientDataJSON"))
    _client_data(raw_cd, "webauthn.get", challenge, origin)
    raw_ad = unb64u(resp.get("authenticatorData"))
    ad = parse_auth_data(raw_ad)
    _flags(ad, rp_id)
    if not verify_sig(key, raw_ad + hashlib.sha256(raw_cd).digest(), unb64u(resp.get("signature"))):
        raise WebAuthnError("signature does not verify")
    if (ad["count"] or stored_count) and ad["count"] <= stored_count:
        raise WebAuthnError("signature counter went backwards -- a cloned key?")
    return ad["count"]
