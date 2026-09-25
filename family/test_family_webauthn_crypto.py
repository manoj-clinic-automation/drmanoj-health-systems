#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_family_webauthn_crypto.py -- OFFLINE (needs the `cryptography` package, which
the server does not have). The hand-written ES256 / RS256 / CBOR checks in
family_webauthn.py, measured against the `cryptography` library:

  K01 ES256: valid signatures from random keys over random messages verify;
  K02 ES256: a changed message, a changed signature, another key, a high r or
      s, and a point off the curve are all refused;
  K03 RS256: valid 2048-bit signatures verify; tampered ones and short keys do not;
  K04 CBOR: a COSE key round-trips; truncated and indefinite items are refused.

    python -B family/test_family_webauthn_crypto.py [count]
Python 3.9.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.environ.get("FAMILY_CODE") or os.path.dirname(os.path.abspath(__file__))))
import family_webauthn as W  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa  # noqa: E402

RES = []


def check(name, ok, detail=""):
    RES.append(bool(ok))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok else "  -- " + str(detail)[:300]), flush=True)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300
    good = bad_ok = 0
    refused = {"msg": 0, "sig": 0, "key": 0, "range": 0, "curve": 0}
    for i in range(n):
        k = ec.generate_private_key(ec.SECP256R1())
        pub = k.public_key().public_numbers()
        msg = os.urandom(1 + i % 97)
        sig = k.sign(msg, ec.ECDSA(hashes.SHA256()))
        good += W.es256_verify(pub.x, pub.y, msg, sig)
        refused["msg"] += not W.es256_verify(pub.x, pub.y, msg + b"x", sig)
        t = bytearray(sig)
        t[-1] ^= 0x01
        refused["sig"] += not W.es256_verify(pub.x, pub.y, msg, bytes(t))
        k2 = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
        refused["key"] += not W.es256_verify(k2.x, k2.y, msg, sig)
        r, s = W.der_sig(sig)
        big = (r + W.N).to_bytes(33, "big")
        hs = b"\x02" + bytes([len(big) + 1]) + b"\x00" + big
        ss = s.to_bytes((s.bit_length() + 8) // 8, "big")
        der = b"\x30" + bytes([len(hs) + 2 + len(ss)]) + hs + b"\x02" + bytes([len(ss)]) + ss
        refused["range"] += not W.es256_verify(pub.x, pub.y, msg, der)
        refused["curve"] += not W.es256_verify(pub.x, (pub.y + 1) % W.P, msg, sig)
    check("K01 ES256: valid signatures verify (%d random keys)" % n, good == n, "%d of %d" % (good, n))
    check("K02 ES256: changed message, signature, key, r out of range and off-curve points are refused",
          all(v == n for v in refused.values()), refused)
    rgood = rbad = 0
    for i in range(max(10, n // 30)):
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pn = k.public_key().public_numbers()
        msg = os.urandom(40)
        sig = k.sign(msg, padding.PKCS1v15(), hashes.SHA256())
        rgood += W.rs256_verify(pn.n, pn.e, msg, sig)
        rbad += (not W.rs256_verify(pn.n, pn.e, msg + b"!", sig)) and \
            (not W.rs256_verify(pn.n, pn.e, msg, sig[:-1] + bytes([sig[-1] ^ 1])))
    m = max(10, n // 30)
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    sm = small.public_key().public_numbers()
    ssig = small.sign(b"m", padding.PKCS1v15(), hashes.SHA256())
    check("K03 RS256: valid signatures verify, tampered ones and 1024-bit keys do not",
          rgood == m and rbad == m and not W.rs256_verify(sm.n, sm.e, b"m", ssig), (rgood, rbad, m))
    k = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    x, y = k.x.to_bytes(32, "big"), k.y.to_bytes(32, "big")
    cose = bytes([0xa5, 0x01, 0x02, 0x03, 0x26, 0x20, 0x01, 0x21, 0x58, 0x20]) + x + bytes([0x22, 0x58, 0x20]) + y
    key = W.cose_key(W.cbor_decode(cose)[0])
    errs = 0
    for bad in (cose[:-5], bytes([0x5f, 0x41, 0x00, 0xff])):
        try:
            W.cbor_decode(bad)
        except W.WebAuthnError:
            errs += 1
    check("K04 CBOR: a COSE key round-trips; truncated and indefinite items are refused",
          key == {"alg": -7, "x": "%x" % k.x, "y": "%x" % k.y} and errs == 2, (key, errs))
    bad = RES.count(False)
    print("\n%d checks, %d failed" % (len(RES), bad))
    print("RESULT: " + ("ALL PASS" if not bad else "FAIL"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
