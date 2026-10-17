"""A blind relay: pairs one 'daemon' and one 'client' socket per room and forwards binary frames untouched.
It logs everything it can see, to prove what a relay operator would learn."""

import asyncio, json, sys, time
from websockets.asyncio.server import serve

rooms = {}
seen = {"frames": 0, "bytes": 0, "plain_hits": []}
# Long markers only: a 3-byte marker such as b"GET" matches random ciphertext by chance (about 0.4 times in 7 MB).
SECRETS = [b"alpha", b"beta", b"x-cuttlefish-token", b"/api/projects", b"/api/auth-mode", b"hello"]


async def handler(ws):
    path = ws.request.path  # /r/<room>/<role>
    _, _, room, role = path.split("/")
    r = rooms.setdefault(room, {})
    r[role] = ws
    peer_role = "client" if role == "daemon" else "daemon"
    try:
        async for m in ws:
            data = m if isinstance(m, bytes) else m.encode()
            seen["frames"] += 1
            seen["bytes"] += len(data)
            for s in SECRETS:
                if s in data:
                    seen["plain_hits"].append((role, s.decode()))
            peer = r.get(peer_role)
            if peer:
                await peer.send(m)
            else:
                print(f"relay: no {peer_role} yet, dropped {len(data)}B", flush=True)
    finally:
        if r.get(role) is ws:
            del r[role]




async def main(port):
    async with serve(handler, "127.0.0.1", port, max_size=2**22):
        print("relay up", port, flush=True)
        while True:
            await asyncio.sleep(1)
            with open(sys.argv[2], "w") as f:
                json.dump(seen, f)


asyncio.run(main(int(sys.argv[1])))
