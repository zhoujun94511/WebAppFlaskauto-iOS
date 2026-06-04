"""一键全跑入口 —— 单元测试 + 全部真机 e2e(含前后端契约联调)。

把散落的检查串成一条命令,按阶段执行并在最后汇总每一档的 PASS/FAIL/SKIP:

    0. build      -> cd frontend && npm run build(刷新 frontend/dist;--no-build 跳过)
    1. unit       -> pytest tests/(纯单测,无需后端)
    2. e2e        -> 自起后端的真机脚本,各自占独立端口、各自起停:
                       smoke(5099) / multidevice(5098) / idle(5097) / webrtc(5096)
    3. contract   -> e2e_frontend_contract.py(它不自起后端,只复用现成的)
                     本入口为它在隔离端口 5095 临时拉一套真实后端 + 同源 SPA,
                     跑完即拆;或用 --base 指向一套已在跑的后端(比如 5099 的
                     ios-ui 预览)直接复用。

每个 e2e 脚本本就把后端隔离在各自端口(5096–5099),互不打架;本入口只额外
为 contract 在 5095 起一套,所以即便你正开着 ios-ui 预览(5099),也能安全并存
——唯一例外是 smoke 也用 5099,若该端口已被占用(预览在跑),smoke 会被跳过
并给出原因,避免一个看不懂的 bind 报错。

用法:
    .venv\\Scripts\\python.exe scripts\\run_checks.py
    .venv\\Scripts\\python.exe scripts\\run_checks.py --no-build --udid <UDID>
    .venv\\Scripts\\python.exe scripts\\run_checks.py --only contract --base http://127.0.0.1:5099
    .venv\\Scripts\\python.exe scripts\\run_checks.py --no-e2e        # 只 unit + contract
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse start_dev's battle-tested port helpers (import has no side effects).
from start_dev import get_port_owners, reset_port  # noqa: E402

IS_WINDOWS = os.name == "nt"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
PY = str(VENV_PY if VENV_PY.exists() else Path(sys.executable))

_GREEN, _RED, _YELLOW, _RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
PASS, FAIL, SKIP = f"{_GREEN}PASS{_RESET}", f"{_RED}FAIL{_RESET}", f"{_YELLOW}SKIP{_RESET}"

# Self-booting real-device suites: (name, script, port-it-binds, accepts --udid).
SELF_BOOT = [
    ("smoke",       "e2e/e2e_smoke.py",        5099, True),
    ("multidevice", "e2e/e2e_multidevice.py",  5098, False),
    ("idle",        "e2e/e2e_idle_release.py", 5097, False),
    ("webrtc",      "e2e/e2e_webrtc.py",       5096, False),
]
CONTRACT_SCRIPT = "e2e/e2e_frontend_contract.py"
CONTRACT_PORT = 5095            # dedicated, distinct from every self-booter + the ios-ui preview
CONTRACT_TUNNEL = 28295         # not the default 28100, nor any e2e script's 28296–28299
CONTRACT_LOCAL_START = 18400    # isolated forward range (smoke/preview use 18500)

ALL_SUITE_NAMES = [n for n, *_ in SELF_BOOT] + ["contract"]


def _hr(title: str) -> None:
    print(f"\n{'=' * 64}\n=== {title}\n{'=' * 64}", flush=True)


def _run(args: list[str], cwd: Path | None = None, **kw) -> int:
    """Run a child, inheriting stdout/stderr so its live PASS/FAIL streams through."""
    print(f"  $ {' '.join(args)}", flush=True)
    return subprocess.run(args, cwd=str(cwd or ROOT), **kw).returncode


def run_build() -> str:
    _hr("0) build SPA  (frontend → dist)")
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    try:
        rc = _run([npm, "run", "build"], cwd=ROOT / "frontend")
    except FileNotFoundError:
        print(f"  [{FAIL}] npm not found on PATH", flush=True)
        return FAIL
    return PASS if rc == 0 else FAIL


def run_unit() -> str:
    _hr("1) unit tests  (pytest tests/)")
    rc = _run([PY, "-m", "pytest", "tests/", "-q"])
    return PASS if rc == 0 else FAIL


def run_self_boot(name: str, script: str, port: int, accepts_udid: bool, udid: str | None) -> str:
    _hr(f"2) e2e:{name}  ({script}  →  binds :{port})")
    if get_port_owners(port):
        print(f"  [{SKIP}] port {port} already in use (ios-ui preview running?) — "
              f"stop it or run this suite alone", flush=True)
        return SKIP
    args = [PY, script]
    if accepts_udid and udid:
        args += ["--udid", udid]
    return PASS if _run(args) == 0 else FAIL


def _boot_contract_backend() -> subprocess.Popen:
    """Bring up a real backend + same-origin SPA on the isolated contract port."""
    if get_port_owners(CONTRACT_PORT):
        reset_port(CONTRACT_PORT)  # reclaim a stale backend from a crashed prior run
    env = os.environ.copy()
    env.update(
        PORT=str(CONTRACT_PORT), HOST="127.0.0.1", OPEN_BROWSER="0",
        IOS_GOIOS_TUNNEL_INFO_PORT=str(CONTRACT_TUNNEL),
        IOS_LOCAL_PORT_START=str(CONTRACT_LOCAL_START),
        IOS_DEVICE_IDLE_TIMEOUT="0",
    )
    base = f"http://127.0.0.1:{CONTRACT_PORT}"
    print(f"  [boot] starting real backend + SPA on :{CONTRACT_PORT} …", flush=True)
    proc = subprocess.Popen(
        [PY, "app.py"], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        with suppress(requests.RequestException):
            if requests.get(base + "/api/health", timeout=2).status_code == 200:
                print("  [boot] backend healthy", flush=True)
                return proc
        time.sleep(1)
    proc.kill()
    raise SystemExit("  [boot] contract backend did not become healthy")


def run_contract(udid: str | None, base: str | None) -> str:
    _hr("3) frontend↔backend contract  (e2e_frontend_contract.py)")
    proc: subprocess.Popen | None = None
    target = base
    try:
        if target is None:
            proc = _boot_contract_backend()
            target = f"http://127.0.0.1:{CONTRACT_PORT}"
        else:
            print(f"  [reuse] running contract against existing backend {target}", flush=True)
        args = [PY, CONTRACT_SCRIPT, "--base", target]
        if udid:
            args += ["--udid", udid]
        return PASS if _run(args) == 0 else FAIL
    finally:
        if proc is not None:
            print("  [teardown] stopping contract backend …", flush=True)
            with suppress(Exception):
                proc.terminate()
                proc.wait(timeout=10)
            if proc.poll() is None:
                with suppress(Exception):
                    proc.kill()
            with suppress(Exception):
                reset_port(CONTRACT_PORT)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="One-click: unit + all real-device e2e (incl. contract).")
    ap.add_argument("--no-build", action="store_true", help="skip 'npm run build'")
    ap.add_argument("--no-unit", action="store_true", help="skip pytest tests/")
    ap.add_argument("--no-e2e", action="store_true", help="skip the self-booting e2e suites")
    ap.add_argument("--no-contract", action="store_true", help="skip the frontend↔backend contract suite")
    ap.add_argument("--only", default=None,
                    help=f"comma list of e2e suites to run, from: {','.join(ALL_SUITE_NAMES)}")
    ap.add_argument("--udid", default=None, help="target device UDID (forwarded to smoke + contract)")
    ap.add_argument("--base", default=None,
                    help="run contract against this already-running backend instead of booting one "
                         "(e.g. http://127.0.0.1:5099 for a live ios-ui preview)")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    if only and (bad := only - set(ALL_SUITE_NAMES)):
        raise SystemExit(f"--only: unknown suite(s) {sorted(bad)}; pick from {ALL_SUITE_NAMES}")

    results: list[tuple[str, str]] = []

    if not args.no_build:
        results.append(("build", run_build()))
        if results[-1][1] == FAIL:
            # A broken SPA build poisons the contract suite (it checks served assets) — stop early.
            _summary(results)
            return 1

    if not args.no_unit and not only:
        results.append(("unit", run_unit()))

    if not args.no_e2e:
        for name, script, port, accepts_udid in SELF_BOOT:
            if only and name not in only:
                continue
            results.append((f"e2e:{name}", run_self_boot(name, script, port, accepts_udid, args.udid)))

    if not args.no_contract and (not only or "contract" in only):
        results.append(("contract", run_contract(args.udid, args.base)))

    return _summary(results)


def _summary(results: list[tuple[str, str]]) -> int:
    _hr("SUMMARY")
    width = max((len(n) for n, _ in results), default=0)
    for name, status in results:
        print(f"  {name.ljust(width)}  {status}", flush=True)
    failed = [n for n, s in results if s == FAIL]
    print("", flush=True)
    if failed:
        print(f"RESULT: {FAIL} — {len(failed)} suite(s) failed: {failed}", flush=True)
        return 1
    print(f"RESULT: {PASS} — all run suites green", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
