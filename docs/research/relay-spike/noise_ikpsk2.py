"""Noise_IKpsk2_25519_AESGCM_SHA256, written from the Noise spec (rev 34) with `cryptography`.

Spike code, not for the repo as is. IK: the initiator (browser) already knows the responder's (daemon's)
static key, learned from the pairing QR code. psk2: a 32-byte secret (a one-time pairing secret the first
time, a per-device secret afterwards) is mixed in at the end of message 2.
"""

import hashlib, hmac, os, struct
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization as ser

NAME = b"Noise_IKpsk2_25519_AESGCM_SHA256"  # 32 bytes, so h = name with no padding needed
assert len(NAME) == 32


def pub_bytes(k: X25519PrivateKey) -> bytes:
    return k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)


def dh(priv: X25519PrivateKey, pub: bytes) -> bytes:
    return priv.exchange(X25519PublicKey.from_public_bytes(pub))


def hkdf(ck: bytes, ikm: bytes, n: int):
    temp = hmac.new(ck, ikm, hashlib.sha256).digest()
    o1 = hmac.new(temp, b"\x01", hashlib.sha256).digest()
    o2 = hmac.new(temp, o1 + b"\x02", hashlib.sha256).digest()
    if n == 2:
        return o1, o2
    o3 = hmac.new(temp, o2 + b"\x03", hashlib.sha256).digest()
    return o1, o2, o3


def _nonce(n: int) -> bytes:
    return b"\x00" * 4 + struct.pack(">Q", n)


class Sym:
    def __init__(self):
        self.h = NAME
        self.ck = NAME
        self.k = None
        self.n = 0

    def mix_hash(self, d):
        self.h = hashlib.sha256(self.h + d).digest()

    def mix_key(self, ikm):
        self.ck, k = hkdf(self.ck, ikm, 2)
        self.k = k
        self.n = 0

    def mix_key_and_hash(self, ikm):
        self.ck, th, k = hkdf(self.ck, ikm, 3)
        self.mix_hash(th)
        self.k = k
        self.n = 0

    def enc_hash(self, pt):
        if self.k is None:
            self.mix_hash(pt)
            return pt
        ct = AESGCM(self.k).encrypt(_nonce(self.n), pt, self.h)
        self.n += 1
        self.mix_hash(ct)
        return ct

    def dec_hash(self, ct):
        if self.k is None:
            self.mix_hash(ct)
            return ct
        pt = AESGCM(self.k).decrypt(_nonce(self.n), ct, self.h)
        self.n += 1
        self.mix_hash(ct)
        return pt


class Transport:
    def __init__(self, send_k, recv_k):
        self.sk, self.rk, self.sn, self.rn = send_k, recv_k, 0, 0

    def encrypt(self, pt: bytes) -> bytes:
        ct = AESGCM(self.sk).encrypt(_nonce(self.sn), pt, b"")
        self.sn += 1
        return ct

    def decrypt(self, ct: bytes) -> bytes:
        pt = AESGCM(self.rk).decrypt(_nonce(self.rn), ct, b"")
        self.rn += 1
        return pt


def _init(prologue: bytes, responder_static_pub: bytes) -> Sym:
    s = Sym()
    s.mix_hash(prologue)
    s.mix_hash(responder_static_pub)
    return s


def initiator_write(s_priv, rs_pub, psk, payload=b"", prologue=b"", e_priv=None):
    sym = _init(prologue, rs_pub)
    e = e_priv or X25519PrivateKey.generate()
    e_pub = pub_bytes(e)
    sym.mix_hash(e_pub)
    sym.mix_key(e_pub)
    sym.mix_key(dh(e, rs_pub))
    enc_s = sym.enc_hash(pub_bytes(s_priv))
    sym.mix_key(dh(s_priv, rs_pub))
    body = sym.enc_hash(payload)
    return e_pub + enc_s + body, (sym, e, s_priv, rs_pub, psk)


def responder_read(s_priv, psk, msg, prologue=b"", e_priv=None, payload=b""):
    """Returns (initiator_static_pub, message2, Transport, payload1). Raises on a bad key, psk or tamper."""
    s_pub = pub_bytes(s_priv)
    sym = _init(prologue, s_pub)
    re = msg[:32]
    sym.mix_hash(re)
    sym.mix_key(re)
    sym.mix_key(dh(s_priv, re))
    rs = sym.dec_hash(msg[32 : 32 + 48])
    sym.mix_key(dh(s_priv, rs))
    p1 = sym.dec_hash(msg[80:])
    e = e_priv or X25519PrivateKey.generate()
    e_pub = pub_bytes(e)
    sym.mix_hash(e_pub)
    sym.mix_key(e_pub)
    sym.mix_key(dh(e, re))
    sym.mix_key(dh(e, rs))
    sym.mix_key_and_hash(psk)
    body = sym.enc_hash(payload)
    k1, k2 = hkdf(sym.ck, b"", 2)
    return rs, e_pub + body, Transport(k2, k1), p1


def initiator_finish(state, msg2):
    sym, e, s_priv, rs_pub, psk = state
    re = msg2[:32]
    sym.mix_hash(re)
    sym.mix_key(re)
    sym.mix_key(dh(e, re))
    sym.mix_key(dh(s_priv, re))
    sym.mix_key_and_hash(psk)
    payload = sym.dec_hash(msg2[32:])
    k1, k2 = hkdf(sym.ck, b"", 2)
    return Transport(k1, k2), payload
