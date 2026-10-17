// Noise_IKpsk2_25519_AESGCM_SHA256, initiator side, WebCrypto only (no libraries).
const NAME = new TextEncoder().encode("Noise_IKpsk2_25519_AESGCM_SHA256");
const cat = (...a) => { const o = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let i = 0; for (const x of a) { o.set(x, i); i += x.length; } return o; };
const sha = async (d) => new Uint8Array(await crypto.subtle.digest("SHA-256", d));
const hmac = async (k, d) => { const key = await crypto.subtle.importKey("raw", k, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]); return new Uint8Array(await crypto.subtle.sign("HMAC", key, d)); };
async function hkdf(ck, ikm, n) {
  const t = await hmac(ck, ikm); const o1 = await hmac(t, Uint8Array.of(1)); const o2 = await hmac(t, cat(o1, Uint8Array.of(2)));
  if (n === 2) return [o1, o2]; return [o1, o2, await hmac(t, cat(o2, Uint8Array.of(3)))];
}
const nonce = (n) => { const b = new Uint8Array(12); new DataView(b.buffer).setBigUint64(4, BigInt(n)); return b; };
async function aesEnc(k, n, ad, pt) { const key = await crypto.subtle.importKey("raw", k, "AES-GCM", false, ["encrypt"]); return new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce(n), additionalData: ad, tagLength: 128 }, key, pt)); }
async function aesDec(k, n, ad, ct) { const key = await crypto.subtle.importKey("raw", k, "AES-GCM", false, ["decrypt"]); return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce(n), additionalData: ad, tagLength: 128 }, key, ct)); }
const importPub = (raw) => crypto.subtle.importKey("raw", raw, { name: "X25519" }, true, []);
const dh = async (priv, pubRaw) => new Uint8Array(await crypto.subtle.deriveBits({ name: "X25519", public: await importPub(pubRaw) }, priv, 256));
const rawPub = async (kp) => new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey));

class Sym {
  constructor() { this.h = NAME; this.ck = NAME; this.k = null; this.n = 0; }
  async mixHash(d) { this.h = await sha(cat(this.h, d)); }
  async mixKey(ikm) { const [ck, k] = await hkdf(this.ck, ikm, 2); this.ck = ck; this.k = k; this.n = 0; }
  async mixKeyAndHash(ikm) { const [ck, th, k] = await hkdf(this.ck, ikm, 3); this.ck = ck; await this.mixHash(th); this.k = k; this.n = 0; }
  async encHash(pt) { if (!this.k) { await this.mixHash(pt); return pt; } const ct = await aesEnc(this.k, this.n++, this.h, pt); await this.mixHash(ct); return ct; }
  async decHash(ct) { if (!this.k) { await this.mixHash(ct); return ct; } const pt = await aesDec(this.k, this.n++, this.h, ct); await this.mixHash(ct); return pt; }
}
export class Transport {
  constructor(sk, rk) { this.sk = sk; this.rk = rk; this.sn = 0; this.rn = 0; this.ek = null; this.dk = null; }
  async encrypt(pt) { this.ek ??= await crypto.subtle.importKey("raw", this.sk, "AES-GCM", false, ["encrypt"]); return new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce(this.sn++), tagLength: 128 }, this.ek, pt)); }
  async decrypt(ct) { this.dk ??= await crypto.subtle.importKey("raw", this.rk, "AES-GCM", false, ["decrypt"]); return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: nonce(this.rn++), tagLength: 128 }, this.dk, ct)); }
}
/** The static key is generated non-extractable: script (and so injected script) can use it but never read it out. */
export const newStaticKey = () => crypto.subtle.generateKey({ name: "X25519" }, false, ["deriveBits"]);

export async function initiatorWrite(sKP, rsPub, psk, payload, prologue) {
  const sym = new Sym(); await sym.mixHash(prologue); await sym.mixHash(rsPub);
  const e = await crypto.subtle.generateKey({ name: "X25519" }, false, ["deriveBits"]); const ePub = await rawPub(e);
  await sym.mixHash(ePub); await sym.mixKey(ePub);
  await sym.mixKey(await dh(e.privateKey, rsPub));
  const encS = await sym.encHash(await rawPub(sKP));
  await sym.mixKey(await dh(sKP.privateKey, rsPub));
  const body = await sym.encHash(payload);
  return { msg: cat(ePub, encS, body), state: { sym, e, sKP, psk } };
}
export async function initiatorFinish(state, msg2) {
  const { sym, e, sKP, psk } = state; const re = msg2.slice(0, 32);
  await sym.mixHash(re); await sym.mixKey(re);
  await sym.mixKey(await dh(e.privateKey, re));
  await sym.mixKey(await dh(sKP.privateKey, re));
  await sym.mixKeyAndHash(psk);
  const payload = await sym.decHash(msg2.slice(32));
  const [k1, k2] = await hkdf(sym.ck, new Uint8Array(0), 2);
  return { transport: new Transport(k1, k2), payload };
}
export const b64 = { enc: (u) => btoa(String.fromCharCode(...u)), dec: (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0)) };
