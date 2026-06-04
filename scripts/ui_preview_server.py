"""Launcher for the Preview-MCP / browser UI 联调.

Boots the real backend on an ISOLATED port (5099) serving the built Vue SPA
from frontend/dist on the same origin — so a headless browser can drive the
actual frontend bundle against the live API + real devices, with no CORS and
no risk to a dev backend on 5001.

Prereq: build the SPA once →  cd frontend && npm run build
Run via Preview MCP (config name "ios-ui" in .claude/launch.json), or directly:
    .venv\\Scripts\\python.exe scripts\\ui_preview_server.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Isolated defaults (override by exporting the same vars before launch).
os.environ.setdefault("PORT", "5099")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("OPEN_BROWSER", "0")
os.environ.setdefault("IOS_GOIOS_TUNNEL_INFO_PORT", "28299")  # not the default 28100
os.environ.setdefault("IOS_LOCAL_PORT_START", "18500")
os.environ.setdefault("IOS_DEVICE_IDLE_TIMEOUT", "0")          # no auto-release during manual testing

# Make the project root importable regardless of the launcher's cwd.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402  (env must be set before this import)

if __name__ == "__main__":
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        print("[ui-preview] frontend/dist not built — run 'npm run build' in frontend/ first",
              flush=True)
    app_module.install_process_lifecycle()
    host = app_module.app.config["HOST"]
    port = app_module.app.config["PORT"]
    print(f"[ui-preview] serving SPA + API on http://{host}:{port}", flush=True)
    app_module.socketio.run(app_module.app, host=host, port=port, allow_unsafe_werkzeug=True)
