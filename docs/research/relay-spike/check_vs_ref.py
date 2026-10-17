# Cross-check my implementation against the independent `noiseprotocol` library, with fixed keys.
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
import noise_ikpsk2 as m
from noise.connection import NoiseConnection, Keypair
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def raw(k):
    return k.private_bytes(m.ser.Encoding.Raw, m.ser.PrivateFormat.Raw, m.ser.NoEncryption())


s_i, e_i, s_r, e_r = (X25519PrivateKey.generate() for _ in range(4))
psk = os.urandom(32)
pro = b"cuttlefish-relay/1"
# reference initiator talking to MY responder
ref = NoiseConnection.from_name(b"Noise_IKpsk2_25519_AESGCM_SHA256")
ref.set_as_initiator()
ref.set_psks(psk)
ref.set_prologue(pro)
ref.set_keypair_from_private_bytes(Keypair.STATIC, raw(s_i))
ref.set_keypair_from_public_bytes(Keypair.REMOTE_STATIC, m.pub_bytes(s_r))
ref.start_handshake()
msg1 = bytes(ref.write_message(b"hello-payload"))
rs, msg2, mine, p1 = m.responder_read(s_r, psk, msg1, prologue=pro, payload=b"ack")
assert rs == m.pub_bytes(s_i) and p1 == b"hello-payload"
assert bytes(ref.read_message(msg2)) == b"ack"
assert ref.handshake_finished
c = bytes(ref.encrypt(b"ref->mine"))
assert mine.decrypt(c) == b"ref->mine"
c = mine.encrypt(b"mine->ref")
assert bytes(ref.decrypt(c)) == b"mine->ref"
print("PASS: my responder agrees with reference initiator (handshake + transport both ways)")
# MY initiator talking to reference responder
ref = NoiseConnection.from_name(b"Noise_IKpsk2_25519_AESGCM_SHA256")
ref.set_as_responder()
ref.set_psks(psk)
ref.set_prologue(pro)
ref.set_keypair_from_private_bytes(Keypair.STATIC, raw(s_r))
ref.start_handshake()
msg1, st = m.initiator_write(s_i, m.pub_bytes(s_r), psk, b"p", prologue=pro)
assert bytes(ref.read_message(msg1)) == b"p"
msg2 = bytes(ref.write_message(b"q"))
t, p2 = m.initiator_finish(st, msg2)
assert p2 == b"q" and ref.handshake_finished
assert bytes(ref.decrypt(t.encrypt(b"x"))) == b"x" and t.decrypt(bytes(ref.encrypt(b"y"))) == b"y"
print("PASS: my initiator agrees with reference responder")
# wrong psk must fail
try:
    m.responder_read(s_r, os.urandom(32), msg1, prologue=pro)
    # psk is mixed after the responder reads; failure shows on the initiator's side
    m2 = m.responder_read(s_r, os.urandom(32), msg1, prologue=pro)[1]
    m.initiator_finish(m.initiator_write(s_i, m.pub_bytes(s_r), psk, b"p", prologue=pro)[1], m2)
    print("FAIL: wrong psk accepted")
except Exception as e:
    print("PASS: wrong psk rejected (", type(e).__name__, ")")
