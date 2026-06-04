"""Device file transfer HTTP API: tree (list) / pull (download) / push (upload).

Scope: the AFC media root (the device's general file area — Downloads, DCIM,
…). No admin, no tunnel. App-sandbox access (house_arrest) isn't reliable
no-admin on iOS 17+, so it's intentionally not exposed.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress

from flask import Blueprint, after_this_request, jsonify, request, send_file

from api import api
from services.ios_file_service import IOSFileService
from services.request_validators import require
from utils.app_errors import AppError, ErrorCode

bp = Blueprint("files", __name__, url_prefix="/api/devices")
_svc = IOSFileService()


@bp.get("/<udid>/files/tree")
@api
def tree(udid: str):
    return _svc.tree(udid, request.args.get("path", "."))


@bp.get("/<udid>/files/pull")
def pull(udid: str):
    """Download a device file. Raw route — streams bytes, not the JSON envelope."""
    src = request.args.get("path")
    if not src:
        return jsonify({"success": False, "code": ErrorCode.BAD_REQUEST,
                        "message": "path required", "detail": {}}), 400
    try:
        local = _svc.pull_to_temp(udid, src)
    except AppError as exc:
        return jsonify(exc.to_dict()), exc.http_status

    @after_this_request
    def _cleanup(response):
        import shutil
        shutil.rmtree(os.path.dirname(local), ignore_errors=True)
        return response

    # ?inline=1 → render in the browser (image/audio/video preview) instead of
    # forcing a download. send_file guesses the mimetype from the filename.
    inline = request.args.get("inline") in ("1", "true", "yes")
    name = os.path.basename(src.rstrip("/")) or "download"
    return send_file(local, as_attachment=not inline, download_name=name)


@bp.post("/<udid>/files/push")
@api
def push(udid: str):
    """Upload a file to the device AFC root (multipart ``file`` + form ``dst_path``)."""
    f = request.files.get("file")
    if f is None or not f.filename:
        raise AppError(ErrorCode.BAD_REQUEST, "no file uploaded (multipart field 'file')")
    require(request.form, "dst_path")
    dst_path = str(request.form["dst_path"])
    fd, local = tempfile.mkstemp(suffix="_upload")
    os.close(fd)
    try:
        f.save(local)
        return _svc.push(udid, local, dst_path)
    finally:
        with suppress(OSError):
            os.remove(local)
