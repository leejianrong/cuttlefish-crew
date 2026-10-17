"""Daemon side: dial out to the relay, finish the handshake as responder, then answer encrypted API requests
by calling the real daemon on loopback. Frames are JSON; a body over 16 KiB is sent in chunks."""

import asyncio, base64, json, os, sys, time
import httpx
from websockets.asyncio.client import connect

sys.path.insert(0, os.path.dirname(__file__))
import noise_ikpsk2 as nz
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

ROOM, RELAY, DAEMON, TOKEN, OUT = sys.argv[1:6]
PSK = bytes.fromhex(os.environ["PAIR_PSK"])
STATIC = X25519PrivateKey.from_private_bytes(bytes.fromhex(os.environ["DAEMON_STATIC"]))
PROLOGUE = b"cuttlefish-relay/1"
CH = 16 * 1024


async def main():
    async with connect(f"{RELAY}/r/{ROOM}/daemon", max_size=2**22) as ws:
        print("connector: waiting for client", flush=True)
        msg1 = await ws.recv()
        t0 = time.perf_counter()
        rs, msg2, tr, p1 = nz.responder_read(
            STATIC, PSK, msg1, prologue=PROLOGUE, payload=b'{"hello":"daemon"}'
        )
        await ws.send(msg2)
        print("connector: handshake done; client static", rs.hex()[:16], "payload", p1, flush=True)
        async with httpx.AsyncClient(
            base_url=DAEMON, headers={"x-cuttlefish-token": TOKEN}
        ) as http:
            async for frame in ws:
                req = json.loads(tr.decrypt(frame))
                if req["path"].startswith("/_blob/"):
                    body = os.urandom(int(req["path"].split("/")[-1]))
                    status = 200
                else:
                    r = await http.request(req["method"], req["path"], content=req.get("body"))
                    body, status = r.content, r.status_code
                if req["path"].startswith("/_blob/"):
                    CH2 = 60000
                else:
                    CH2 = CH
                if req.get("bin"):
                    chunks = [body[i : i + CH2] for i in range(0, len(body), CH2)] or [b""]
                    await ws.send(
                        tr.encrypt(
                            json.dumps(
                                {
                                    "id": req["id"],
                                    "status": status,
                                    "n": len(chunks),
                                    "len": len(body),
                                }
                            ).encode()
                        )
                    )
                    for ch in chunks:
                        await ws.send(tr.encrypt(ch))
                    continue
                chunks = [body[i : i + CH] for i in range(0, len(body), CH)] or [b""]
                await ws.send(
                    tr.encrypt(
                        json.dumps(
                            {"id": req["id"], "status": status, "n": len(chunks), "len": len(body)}
                        ).encode()
                    )
                )
                for c in chunks:
                    await ws.send(
                        tr.encrypt(
                            json.dumps(
                                {"id": req["id"], "d": base64.b64encode(c).decode()}
                            ).encode()
                        )
                    )


asyncio.run(main())
