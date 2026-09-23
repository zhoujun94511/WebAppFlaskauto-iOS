"""tunnel ls output must not treat a go-ios warning as a live tunnel."""

import sys

from ios.tunnel_manager import tunnels_listed
from services.command_runner import run_command


def test_warning_plus_empty_list_is_not_ready():
    raw = (
        '{"level":"warning","msg":"go-ios agent is not running. You might need to start it"}\n'
        "[]"
    )
    assert tunnels_listed(raw) is False


def test_warning_plus_a_tunnel_is_ready():
    raw = (
        '{"level":"warning","msg":"go-ios agent is not running"}\n'
        '[{"udid":"00008130-001251861A02001C","address":"127.0.0.1","rsdPort":54923}]'
    )
    assert tunnels_listed(raw) is True


def test_connection_refused_has_no_list():
    assert tunnels_listed("dial tcp 127.0.0.1:28100: connectex: No connection could be made") is None


def test_run_command_keeps_bytes_gbk_cannot_decode():
    res = run_command(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok\\x80\\n')"],
        timeout=15,
    )
    assert res.returncode == 0
    assert "ok" in res.stdout
    assert "\ufffd" in res.stdout
