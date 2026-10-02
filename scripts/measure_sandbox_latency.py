"""Measure what running kopicode ``serve`` inside a container per delegation costs (KAN-1793).

Three arms, each running the same do-nothing delegation to completion with an *invalid* key,
so no model tokens are spent and the model's own time is out of the number:

- ``host-fresh``  one new ``kopicode serve`` child per delegation, on the host
- ``host-pooled`` a resident child reused across delegations (the unsandboxed default)
- ``container``   a new container, ``docker exec -i`` child, session and teardown per delegation

Every arm still makes one real request to the provider (it answers 401), so each includes the
same network round trip. The container arm is also split into create / session / destroy.
Needs docker, kopicode >= v0.2.0 on PATH, and outbound network. Run:
``uv run python scripts/measure_sandbox_latency.py [runs]``.
"""

from __future__ import annotations

import asyncio
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.delegate.consent import ConsentPolicy
from cuttlefish.delegate.kopicode_serve import ServePool, run_kopicode_serve
from cuttlefish.sandbox.container import ContainerSandboxProvider
from cuttlefish.sandbox.provider import SandboxHandle, SandboxSpec

KEY = "sk-or-v1-" + "0" * 64  # invalid on purpose: the provider refuses it, nothing is spent


class _TimedProvider(ContainerSandboxProvider):
    """The real container provider, timing its own create and destroy."""

    def __init__(self) -> None:
        super().__init__()
        self.create_ms: list[float] = []
        self.destroy_ms: list[float] = []

    async def create(self, spec: SandboxSpec | None = None) -> SandboxHandle:
        start = time.perf_counter()
        handle = await super().create(spec)
        self.create_ms.append((time.perf_counter() - start) * 1000)
        return handle

    async def destroy(self, handle: SandboxHandle) -> None:
        start = time.perf_counter()
        await super().destroy(handle)
        self.destroy_ms.append((time.perf_counter() - start) * 1000)


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", str(path)],
        check=True,
        cwd=path,
        env={"PATH": "/usr/bin:/bin"},
    )


async def _time(runs: int, one: Callable[[], Awaitable[object]]) -> list[float]:
    await one()  # warm-up, discarded: first run pays image/page-cache costs
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        await one()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def _row(name: str, samples: list[float]) -> str:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return (
        f"{name:<26} median {statistics.median(samples):7.0f} ms   "
        f"min {ordered[0]:7.0f}   p95 {p95:7.0f}   max {ordered[-1]:7.0f}   (n={len(samples)})"
    )


async def main(runs: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _git_init(root)
        env = {"OPENROUTER_API_KEY": KEY}

        async def host_fresh() -> object:
            return await run_kopicode_serve(
                binary="kopicode",
                task_text="noop",
                root=str(root),
                policy=ConsentPolicy(),
                env=env,
                timeout=120,
            )

        pool = ServePool()

        async def host_pooled() -> object:
            return await run_kopicode_serve(
                binary="kopicode",
                task_text="noop",
                root=str(root),
                policy=ConsentPolicy(),
                env=env,
                timeout=120,
                pool=pool,
            )

        provider = _TimedProvider()
        backend = KopicodeBackend("kopicode")

        async def container() -> object:
            return await backend.delegate(
                task_text="noop",
                root=str(root),
                allow=None,
                secrets=env,
                sandbox_provider=provider,
            )

        results = {
            "host, fresh child": await _time(runs, host_fresh),
            "host, pooled child": await _time(runs, host_pooled),
            "container per delegation": await _time(runs, container),
        }
        await pool.aclose()

    print(f"\n{runs} runs per arm after one discarded warm-up; invalid key, so no model cost\n")
    for name, samples in results.items():
        print(_row(name, samples))
    total = results["container per delegation"]
    create, destroy = provider.create_ms[1:], provider.destroy_ms[1:]  # drop the warm-up
    print("\ncontainer arm, split (medians):")
    print(f"  docker run (create)   {statistics.median(create):7.0f} ms")
    print(f"  docker rm -f (destroy){statistics.median(destroy):7.0f} ms")
    rest = statistics.median(total) - statistics.median(create) - statistics.median(destroy)
    print(f"  exec + session + close{rest:7.0f} ms")
    fresh = statistics.median(results["host, fresh child"])
    pooled = statistics.median(results["host, pooled child"])
    print(f"\ncontainer vs host fresh:  +{statistics.median(total) - fresh:.0f} ms")
    print(f"container vs host pooled: +{statistics.median(total) - pooled:.0f} ms")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10))
