"""Thin wrapper around the bundled go-ios (`ios`) binary.

go-ios is used ONLY for the few things pymobiledevice3 can't do no-admin on
Windows for iOS 17+: the userspace RemoteServiceDiscovery tunnel and launching
WebDriverAgent (`runwda`) over it, plus DeveloperDiskImage auto-mount and a
Developer-Mode check. Everything else stays on pymobiledevice3 (Flask-native).

Binary resolution: prefer the bundled per-OS executable under
``resources/executable/<os>/``; otherwise extract ``resources/utils/
go-ios-<os>.zip`` into ``resources/runpath/<os>/`` on first use.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import threading
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.logging_setup import get_logger

_log = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_RES = _ROOT / "resources"
_IS_WIN = platform.system() == "Windows"
# Userspace agent env — required for the no-admin tunnel on Windows.
_AGENT_ENV = {"ENABLE_GO_IOS_AGENT": "user"}


class GoIOSError(RuntimeError):
    pass


class GoIOS:
    def __init__(
        self,
        bin_override: Optional[str] = None,
        prefer_system: bool = False,
        tunnel_info_port: int = 28100,
    ):
        self._explicit: Optional[str] = bin_override or None
        self.prefer_system = prefer_system
        # Pin the agent's HTTP-API port so the tunnel agent is deterministic
        # and reclaimable (an unpinned agent picks an ephemeral port that we
        # can't find/kill after a crash). 28100 is go-ios's own default.
        self.tunnel_info_port = int(tunnel_info_port)
        self._bin: Optional[str] = None
        self._lock = threading.Lock()

    # ── binary resolution ───────────────────────────────────────────
    @staticmethod
    def _os_key() -> str:
        s = platform.system().lower()
        if s.startswith("win"):
            return "win"
        if s == "darwin":
            return "mac"
        return "linux"

    @staticmethod
    def _usable(path: Optional[str]) -> bool:
        """A candidate is only accepted if it actually runs (`ios version`).
        Guards against a broken / wrong-arch / name-squatting binary on PATH."""
        if not path:
            return False
        try:
            flags = subprocess.CREATE_NO_WINDOW if _IS_WIN else 0
            cp = subprocess.run(
                [path, "version"], capture_output=True, text=True, timeout=10,
                creationflags=flags,
            )
            return cp.returncode == 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _system_path() -> Optional[str]:
        """First `ios`/`ios.exe` on PATH (NOT our bundled one)."""
        for name in ("ios", "ios.exe", "go-ios", "go-ios.exe"):
            p = shutil.which(name)
            if p:
                return p
        return None

    def binary(self) -> str:
        with self._lock:
            if self._bin:
                return self._bin

            os_key = self._os_key()
            exe_dir = _RES / "executable" / os_key
            bundled = self._find_exe(exe_dir)
            bundled = str(bundled) if bundled else None
            system = self._system_path()

            # Resolution order. ``IOS_GOIOS_PREFER_SYSTEM`` flips system<->bundled.
            # Every candidate must pass the `version` usability check.
            candidates: List[Optional[str]] = [self._explicit]
            if self.prefer_system:
                candidates += [system, bundled]
            else:
                candidates += [bundled, system]

            for cand in candidates:
                if cand and self._usable(cand):
                    src = (
                        "override" if cand == self._explicit
                        else "system" if cand == system
                        else "bundled"
                    )
                    _log.info("using go-ios (%s): %s", src, cand)
                    self._bin = cand
                    return self._bin

            if self._explicit and not self._usable(self._explicit):
                raise GoIOSError(f"IOS_GOIOS_BIN not usable: {self._explicit}")

            # Last resort: extract the per-OS zip into runpath/ and use it.
            zip_path = _RES / "utils" / f"go-ios-{os_key}.zip"
            if zip_path.exists():
                target = _RES / "runpath" / os_key
                target.mkdir(parents=True, exist_ok=True)
                if not self._find_exe(target):
                    try:
                        with zipfile.ZipFile(zip_path) as zf:
                            zf.extractall(target)
                    except zipfile.BadZipFile as exc:
                        raise GoIOSError(f"corrupt go-ios zip: {zip_path}") from exc
                found = self._find_exe(target)
                if found:
                    self._ensure_exec_bit(found)
                    if self._usable(str(found)):
                        _log.info("using go-ios (extracted): %s", found)
                        self._bin = str(found)
                        return self._bin
            raise GoIOSError(
                "no usable go-ios binary found "
                f"(checked PATH, {exe_dir}, and {zip_path})"
            )

    @staticmethod
    def _find_exe(search: Path) -> Optional[Path]:
        if not search.exists():
            return None
        names = ["ios.exe", "ios", "ios-amd64", "ios-arm64", "go-ios", "go-ios.exe"]
        files = {p.name: p for p in search.rglob("*") if p.is_file()}
        for n in names:
            if n in files:
                return files[n]
        return None

    @staticmethod
    def _ensure_exec_bit(path: Path) -> None:
        if os.name != "nt":
            try:
                path.chmod(path.stat().st_mode | stat.S_IEXEC)
            except OSError:
                pass

    def is_available(self) -> bool:
        try:
            self.binary()
            return True
        except GoIOSError:
            return False

    def version(self) -> str:
        """Return the go-ios version string (e.g. 'v1.0.182'), or '?'."""
        import json

        code, out, err = self.run(["version"], timeout=10, agent=False)
        if code != 0:
            return "?"
        for line in (out or err).strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return str(json.loads(line).get("version", "?"))
                except ValueError:
                    continue
        return (out or "?").strip().splitlines()[-1] if out else "?"

    # ── run ──────────────────────────────────────────────────────────
    def run(
        self,
        args: List[str],
        timeout: int = 60,
        udid: Optional[str] = None,
        agent: bool = False,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str, str]:
        # agent=False by default: plain commands (list/version/devmode/kill)
        # do NOT need — and must not spawn — a tunnel agent. Only tunnel/dev
        # commands pass agent=True.
        cmd = [self.binary()]
        if udid:
            cmd += ["--udid", udid]
        cmd += args
        env = os.environ.copy()
        if agent:
            env.update(_AGENT_ENV)
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})
        flags = subprocess.CREATE_NO_WINDOW if _IS_WIN else 0
        try:
            cp = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                env=env, encoding="utf-8", errors="replace", creationflags=flags,
            )
            return cp.returncode, cp.stdout or "", cp.stderr or ""
        except subprocess.TimeoutExpired:
            return 124, "", f"go-ios timed out: {' '.join(args)}"
        except (OSError, ValueError) as exc:
            return 125, "", f"go-ios exec error: {exc}"

    def popen(
        self,
        args: List[str],
        udid: Optional[str] = None,
        agent: bool = False,
        extra_env: Optional[Dict[str, str]] = None,
        log_file: bool = False,
    ) -> subprocess.Popen:
        """Long-running command (tunnel agent, runwda). Output normally goes to
        DEVNULL (go-ios prints carriage-return progress that would stall an
        unread pipe). With ``log_file=True`` it's redirected to a temp file
        instead — non-blocking, and lets the caller read WHY a launcher (runwda)
        died early (signing / trust / device-locked, etc.). The file path is
        stashed on the returned Popen as ``_goios_log``.
        """
        cmd = [self.binary()]
        if udid:
            cmd += ["--udid", udid]
        cmd += args
        env = os.environ.copy()
        if agent:
            env.update(_AGENT_ENV)
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})
        flags = (
            (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            if _IS_WIN else 0
        )
        if log_file:
            import tempfile
            lf = tempfile.NamedTemporaryFile(prefix="goios_wda_", suffix=".log", delete=False)
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, creationflags=flags,
            )
            lf.close()  # child keeps its own fd; we only needed the path
            proc._goios_log = lf.name  # type: ignore[attr-defined]
            return proc
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, creationflags=flags,
        )

    def syslog_popen(self, udid: str, parse: bool = True) -> subprocess.Popen:
        """Stream the device's live system log (``ios syslog``). Long-running →
        stdout is a line-buffered text PIPE the caller drains; the caller owns
        the process lifecycle. iOS 17+ needs the userspace tunnel, so this runs
        with the agent env (reusing an already-running tunnel from connect)."""
        args = ["syslog"]
        if parse:
            args.append("--parse")  # go-ios formats the fields (verified --help)
        cmd = [self.binary()]
        if udid:
            cmd += ["--udid", udid]
        cmd += args
        env = os.environ.copy()
        env.update(_AGENT_ENV)
        flags = (
            (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            if _IS_WIN else 0
        )
        return subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, encoding="utf-8", errors="replace", bufsize=1,
            universal_newlines=True, creationflags=flags,
        )

    # ── convenience ──────────────────────────────────────────────────
    def list_devices(self) -> List[str]:
        import json

        code, out, _ = self.run(["list"], timeout=15)
        if code != 0:
            return []
        try:
            data = json.loads(out.strip().splitlines()[-1])
            return list(data.get("deviceList", []))
        except (ValueError, IndexError):
            return []

    def list_apps(self, udid: str, system: bool = False) -> List[Dict[str, str]]:
        """Installed apps via ``ios apps --list`` (bundleId, name, version).
        Works over usbmux/installation-proxy — no WDA/tunnel needed."""
        args = ["apps", "--list"]
        if system:
            args.append("--system")
        code, out, _ = self.run(args, udid=udid, timeout=40)
        if code != 0:
            return []
        apps: List[Dict[str, str]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("{"):  # skip blanks / stray JSON logs
                continue
            parts = line.split()
            bundle = parts[0]
            # Format: "<bundleId> <name maybe with spaces> <version>".
            if len(parts) >= 3:
                version, name = parts[-1], " ".join(parts[1:-1])
            elif len(parts) == 2:
                version, name = "", parts[1]
            else:
                version, name = "", bundle
            apps.append({"bundle_id": bundle, "name": name or bundle, "version": version})
        return apps

    # ── file transfer (AFC media root, or an app's Documents via --app) ──
    @staticmethod
    def _fsync_args(app: Optional[str]) -> List[str]:
        return ["fsync"] + ([f"--app={app}"] if app else [])

    def fsync_tree(self, udid: str, path: str = ".", app: Optional[str] = None) -> str:
        args = self._fsync_args(app) + ["tree", f"--path={path}"]
        code, out, err = self.run(args, udid=udid, timeout=60)
        return out if code == 0 else (out or err)

    def fsync_pull(self, udid: str, src_path: str, dst_local: str,
                   app: Optional[str] = None) -> Tuple[bool, str]:
        args = self._fsync_args(app) + ["pull", f"--srcPath={src_path}", f"--dstPath={dst_local}"]
        code, out, err = self.run(args, udid=udid, timeout=300)
        return code == 0, (out or err).strip()

    def fsync_push(self, udid: str, src_local: str, dst_path: str,
                   app: Optional[str] = None) -> Tuple[bool, str]:
        args = self._fsync_args(app) + ["push", f"--srcPath={src_local}", f"--dstPath={dst_path}"]
        code, out, err = self.run(args, udid=udid, timeout=600)
        return code == 0, (out or err).strip()

    def install_app(self, udid: str, ipa_path: str) -> Tuple[bool, str]:
        code, out, err = self.run(["install", f"--path={ipa_path}"], udid=udid, timeout=600)
        return code == 0, (out or err).strip()

    def uninstall_app(self, udid: str, bundle_id: str) -> Tuple[bool, str]:
        code, out, err = self.run(["uninstall", bundle_id], udid=udid, timeout=120)
        return code == 0, (out or err).strip()

    def devmode_get(self, udid: str) -> str:
        code, out, err = self.run(["devmode", "get"], udid=udid, timeout=30)
        low = (out + err).lower()
        if "true" in low or "enabled" in low:
            return "on"
        if "false" in low or "disabled" in low:
            return "off"
        return "unknown"

    def image_auto(self, udid: str, basedir: Optional[str] = None) -> Tuple[bool, str]:
        """Auto-resolve + mount the DeveloperDiskImage (needed for iOS<17 dev
        services; iOS17+ uses the tunnel instead). Best-effort."""
        args = ["image", "auto"]
        if basedir:
            args.append(f"--basedir={basedir}")
        code, out, err = self.run(args, udid=udid, timeout=300)
        return code == 0, (out or err).strip()

    # Developer Disk Images are cached under resources/devimages/ by default.
    DEFAULT_DEVIMAGE_DIR = _RES / "devimages"

    def mount_developer_image(
        self, udid: str, basedir: Optional[str] = None, timeout: int = 180
    ) -> Tuple[bool, str]:
        """Ensure the Developer Disk Image is mounted (no-admin, via the tunnel).
        iOS 17+ xctest/WDA can't start an IDE session without it ("cannot
        initiate an IDE session: ... broken pipe"). ``image auto`` downloads the
        matching DDI into ``basedir`` (cached) and mounts it; calling it when
        already mounted is a fast no-op. ``basedir`` defaults to
        ``resources/devimages``. Returns (ok, message)."""
        cache = basedir or str(self.DEFAULT_DEVIMAGE_DIR)
        try:
            os.makedirs(cache, exist_ok=True)
        except OSError:
            pass
        code, out, err = self.run(
            ["image", "auto", f"--basedir={cache}"], udid=udid, agent=True, timeout=int(timeout),
        )
        blob = f"{out}\n{err}"
        low = blob.lower()
        # go-ios exits 0 even when the mount fails (e.g. it resolved the WRONG
        # DDI for a too-new iOS and the personalize identity doesn't match), so
        # exit code alone is unreliable — inspect the output for an error marker.
        # "already ... mounted" + "success mounting image" is a benign re-mount.
        ok = (
            "error mounting image" not in low
            and "could not find identity" not in low
            and "failed to find identity" not in low
            and '"err":' not in low
        )
        if not ok:
            # Tail the most relevant error line for the caller to surface.
            errline = next(
                (ln for ln in blob.splitlines() if "could not find identity" in ln.lower()
                 or "error mounting" in ln.lower() or '"err":' in ln.lower()),
                blob.strip()[-300:],
            )
            return False, errline[-400:]
        return True, (err or out)

    def run_wda(
        self,
        udid: str,
        bundle_id: str,
        test_runner_bundle_id: Optional[str] = None,
        xctest_config: str = "WebDriverAgentRunner.xctest",
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.Popen:
        """Launch WebDriverAgent via ``ios runwda`` (uses go-ios's own tunnel,
        no admin). Long-running → returns the Popen; caller manages lifecycle.
        ``env`` lets us pass USE_PORT / MJPEG_SERVER_PORT into WDA."""
        trb = test_runner_bundle_id or bundle_id
        args = [
            "runwda",
            f"--bundleid={bundle_id}",
            f"--testrunnerbundleid={trb}",
            f"--xctestconfig={xctest_config}",
        ]
        # WDA reads USE_PORT / MJPEG_SERVER_PORT from its env; go-ios forwards
        # ``--env KEY=VALUE`` flags into the test runner.
        for k, v in (env or {}).items():
            args.append(f"--env={k}={v}")
        # runwda is a dev service → needs the agent (tunnel) on the default
        # info port, which TunnelManager has pinned to self.tunnel_info_port.
        # log_file=True so a fast-failing launch leaves its reason on disk.
        return self.popen(args, udid=udid, agent=True, log_file=True)
