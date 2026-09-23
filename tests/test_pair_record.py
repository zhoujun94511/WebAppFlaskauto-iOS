"""A newer lockdown .plist.tmp replaces the HostID go-ios is still sending."""

import os
import plistlib

from ios.pair_record import promote_pending_pair_record


def _record(host_id: str) -> dict:
    return {
        "HostID": host_id,
        "SystemBUID": "buid",
        "HostCertificate": b"cert",
        "HostPrivateKey": b"key",
    }


def test_newer_tmp_replaces_stale_host_id(tmp_path):
    udid = "00008130-001251861A02001C"
    official = tmp_path / f"{udid}.plist"
    pending = tmp_path / f"{udid}.plist.tmp"
    official.write_bytes(plistlib.dumps(_record("old-host")))
    pending.write_bytes(plistlib.dumps(_record("new-host")))
    os.utime(official, (1_000_000_000, 1_000_000_000))
    os.utime(pending, (1_700_000_000, 1_700_000_000))
    pmd3 = tmp_path / "pmd3"
    pmd3.mkdir()

    assert promote_pending_pair_record(udid, folder=tmp_path, pmd3_dir=pmd3) is True
    assert not pending.exists()
    assert plistlib.loads(official.read_bytes())["HostID"] == "new-host"
    assert plistlib.loads((pmd3 / f"{udid}.plist").read_bytes())["HostID"] == "new-host"
    assert (tmp_path / f"{udid}.plist.bak").is_file()


def test_same_host_id_is_left_alone(tmp_path):
    udid = "device"
    body = plistlib.dumps(_record("same"))
    (tmp_path / f"{udid}.plist").write_bytes(body)
    (tmp_path / f"{udid}.plist.tmp").write_bytes(body)
    assert promote_pending_pair_record(udid, folder=tmp_path, pmd3_dir=tmp_path / "missing") is False
