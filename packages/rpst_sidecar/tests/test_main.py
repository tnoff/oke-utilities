"""Tests for the refresh loop, file writing, audit line and --check."""

import logging
import os
import signal
import stat
import time
import threading
from pathlib import Path
from unittest import mock

import pytest

from rpst_sidecar import exchange as ex
from rpst_sidecar import main as m
from rpst_sidecar.exchange import ExchangeError

from conftest import DOMAIN, SA_TOKEN
from helpers import http_error, make_jwt, ok_response

NOW = 1_800_000_000


def rpst_for(jti="jti-1", iat=NOW, exp=NOW + 10800):
    """An RPST JWT with the claims the sidecar reads."""
    return make_jwt(jti=jti, iat=iat, exp=exp)


@pytest.fixture
def sidecar(cfg, key):
    """A sidecar using the shared key."""
    return m.Sidecar(cfg, key)


def patch_exchange(*results):
    """Patch urlopen to return/raise each result in turn."""
    return mock.patch.object(ex, "urlopen", side_effect=list(results))


# ---- file writing -------------------------------------------------------------------

def test_refresh_writes_both_files(sidecar, cfg):
    rpst = rpst_for()
    with patch_exchange(ok_response(rpst)):
        delay = sidecar.refresh_once()
    out = Path(cfg.output_dir)
    assert (out / "rpst").read_bytes() == rpst.encode()
    assert not (out / "rpst").read_bytes().endswith(b"\n")
    assert (out / "private.pem").read_bytes() == ex.private_pem(sidecar.key)
    for name in ("rpst", "private.pem"):
        assert stat.S_IMODE(os.stat(out / name).st_mode) == 0o640
    assert delay == 10800 * 0.5
    assert sorted(p.name for p in out.iterdir()) == ["private.pem", "rpst"]  # no temp files left


def test_key_reused_across_exchanges(sidecar, cfg):
    out = Path(cfg.output_dir)
    with patch_exchange(ok_response(rpst_for("a")), ok_response(rpst_for("b"))) as urlopen:
        sidecar.refresh_once()
        first_pem, first_rpst = (out / "private.pem").read_bytes(), (out / "rpst").read_bytes()
        sidecar.refresh_once()
    assert (out / "private.pem").read_bytes() == first_pem
    assert (out / "rpst").read_bytes() != first_rpst
    forms = [call.args[0].data for call in urlopen.call_args_list]
    assert forms[0] == forms[1]  # same public key and inputs both times


def test_private_key_not_rewritten(sidecar, cfg):
    with patch_exchange(ok_response(rpst_for()), ok_response(rpst_for())):
        sidecar.refresh_once()
        with mock.patch.object(m, "write_atomic", wraps=m.write_atomic) as writer:
            sidecar.refresh_once()
    assert [c.args[0].name for c in writer.call_args_list] == ["rpst"]


def test_failed_exchange_leaves_files_alone(sidecar, cfg):
    out = Path(cfg.output_dir)
    with patch_exchange(ok_response(rpst_for("old")), http_error(500, "nope")):
        sidecar.refresh_once()
        before = (out / "rpst").read_bytes()
        with pytest.raises(ExchangeError):
            sidecar.refresh_once()
    assert (out / "rpst").read_bytes() == before


def test_private_key_written_before_rpst(sidecar, cfg):
    order = []
    real = m.write_atomic
    with patch_exchange(ok_response(rpst_for())), \
            mock.patch.object(m, "write_atomic", side_effect=lambda p, d: (order.append(p.name), real(p, d))):
        sidecar.refresh_once()
    assert order == ["private.pem", "rpst"]


def test_generates_key_when_none_given(cfg):
    assert m.Sidecar(cfg).key.key_size == 2048


def test_write_atomic_replaces_whole_file(tmp_path):
    target = tmp_path / "f"
    target.write_bytes(b"old")
    m.write_atomic(target, b"new")
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o640


def test_write_atomic_failure_cleans_up_and_keeps_old(tmp_path):
    target = tmp_path / "f"
    target.write_bytes(b"old")
    with mock.patch.object(m.os, "fsync", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            m.write_atomic(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_unwritable_output_dir_is_a_failure(sidecar, cfg):
    os.rmdir(cfg.output_dir)
    with patch_exchange(ok_response(rpst_for())):
        with pytest.raises(OSError):
            sidecar.refresh_once()


# ---- scheduling ----------------------------------------------------------------------

def test_lifetime_from_claims(cfg):
    assert m.lifetime_seconds({"iat": 100, "exp": 1100}, 180) == 1000
    assert m.refresh_delay({"iat": 100, "exp": 1100}, cfg) == 500
    cfg.refresh_fraction = 0.8
    assert m.refresh_delay({"iat": 100, "exp": 1100}, cfg) == 800


@pytest.mark.parametrize("claims", [
    {}, {"exp": 5000}, {"iat": 5000}, {"iat": "x", "exp": "y"}, {"iat": 10, "exp": 10},
    {"iat": 20, "exp": 10}, {"iat": True, "exp": 1000},
])
def test_lifetime_falls_back_to_requested_minutes(claims):
    assert m.lifetime_seconds(claims, 120) == 7200


def test_refresh_delay_fallback(cfg):
    cfg.exp_minutes = 60
    assert m.refresh_delay({}, cfg) == 1800


def test_refresh_once_uses_fallback_for_opaque_token(sidecar, cfg):
    cfg.exp_minutes = 60
    with patch_exchange(ok_response("opaque-not-a-jwt")):
        assert sidecar.refresh_once() == 1800


# ---- audit ---------------------------------------------------------------------------

def test_audit_line(sidecar, caplog):
    caplog.set_level(logging.INFO)
    rpst = rpst_for(jti="jti-xyz", iat=NOW, exp=NOW + 3 * 3600)
    with patch_exchange(ok_response(rpst)):
        sidecar.refresh_once()
    lines = [r.getMessage() for r in caplog.records if "rpst issued" in r.getMessage()]
    assert len(lines) == 1
    line = lines[0]
    assert "\n" not in line
    for expected in (
        "jti=jti-xyz",
        "sub=system:serviceaccount:apps:bot",
        "namespace=apps",
        "pod=pod-1",
        "service_account=bot",
        "issued=2027-01-15T08:00:00Z",
        "expires=2027-01-15T11:00:00Z",
        "lifetime_minutes=180.0",
    ):
        assert expected in line
    assert rpst not in caplog.text
    assert SA_TOKEN not in caplog.text
    assert "the-client-secret" not in caplog.text


def test_audit_line_with_unreadable_tokens(sidecar, caplog):
    caplog.set_level(logging.INFO)
    sidecar.audit({}, {})
    line = caplog.records[-1].getMessage()
    assert "jti=unknown" in line and "sub=unknown" in line
    assert "issued=unknown" in line and "expires=unknown" in line and "lifetime_minutes=unknown" in line


# ---- loop: retry, backoff, stop ------------------------------------------------------

class StopAfter(threading.Event):
    """An Event whose wait() records each requested sleep and sets itself after `limit` calls."""

    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) >= self.limit:
            self.set()
        return self.is_set()


def no_jitter(delay):
    """Deterministic jitter for tests."""
    return delay


def test_backoff_grows_then_caps(sidecar):
    stop = StopAfter(10)
    with patch_exchange(*[http_error(401, "denied")] * 10):
        assert sidecar.run(stop, no_jitter) == 0
    assert stop.waits == [2, 4, 8, 16, 32, 64, 120, 120, 120, 120]


def test_default_jitter_stays_inside_the_window():
    for _ in range(200):
        assert 1.0 <= m.default_jitter(2.0) <= 2.0
        assert 60.0 <= m.default_jitter(120.0) <= 120.0


def test_backoff_resets_after_success(sidecar):
    stop = StopAfter(5)
    with patch_exchange(
        http_error(500, "x"), http_error(500, "x"), ok_response(rpst_for()), http_error(500, "x"), http_error(500, "x"),
    ):
        sidecar.run(stop, no_jitter)
    assert stop.waits == [2, 4, 5400, 2, 4]


def test_retries_forever_at_startup_then_recovers(sidecar, cfg):
    stop = StopAfter(4)
    with patch_exchange(*[http_error(400, "not yet")] * 3, ok_response(rpst_for())):
        sidecar.run(stop, no_jitter)
    assert (Path(cfg.output_dir) / "rpst").exists()
    assert stop.waits[:3] == [2, 4, 8]


def test_failure_warning_has_status_and_body_but_no_secrets(sidecar, caplog):
    caplog.set_level(logging.INFO)
    body = "denied " + "y" * 400 + " the-client-secret " + SA_TOKEN
    stop = StopAfter(1)
    with patch_exchange(http_error(403, body)):
        sidecar.run(stop, no_jitter)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert "HTTP 403" in text and "denied" in text
    assert len(text) < 400
    for secret in ("the-client-secret", SA_TOKEN, "Basic "):
        assert secret not in caplog.text


def test_sleeps_for_computed_delay_on_success(sidecar):
    stop = StopAfter(1)
    with patch_exchange(ok_response(rpst_for(iat=NOW, exp=NOW + 1000))):
        sidecar.run(stop, no_jitter)
    assert stop.waits == [500]


def test_oserror_from_volume_is_retried(sidecar, cfg):
    stop = StopAfter(2)
    os.rmdir(cfg.output_dir)
    with patch_exchange(ok_response(rpst_for()), ok_response(rpst_for())):
        sidecar.run(stop, no_jitter)
    assert stop.waits == [2, 4]


def test_stop_already_set_does_nothing(sidecar):
    stop = threading.Event()
    stop.set()
    with mock.patch.object(ex, "urlopen") as urlopen:
        assert sidecar.run(stop) == 0
    urlopen.assert_not_called()


def test_stop_during_sleep_exits_cleanly(sidecar):
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    with patch_exchange(ok_response(rpst_for())):
        assert sidecar.run(stop) == 0  # real wait(5400) is cut short by stop.set()


# ---- --check -------------------------------------------------------------------------

def write_outputs(cfg, rpst, pem=True):
    out = Path(cfg.output_dir)
    if rpst is not None:
        (out / "rpst").write_text(rpst)
    if pem:
        (out / "private.pem").write_text("pem")


def test_check_ok(cfg):
    write_outputs(cfg, rpst_for(exp=NOW + 3600))
    with mock.patch.object(m, "now", return_value=NOW):
        assert m.check(cfg) is None


def test_check_missing_rpst(cfg):
    write_outputs(cfg, None)
    assert "cannot read" in m.check(cfg)


def test_check_missing_private_key(cfg):
    write_outputs(cfg, rpst_for(), pem=False)
    assert "private.pem does not exist" in m.check(cfg)


def test_check_expired(cfg):
    write_outputs(cfg, rpst_for(exp=NOW - 10))
    with mock.patch.object(m, "now", return_value=NOW):
        assert "expires in -10s" in m.check(cfg)


@pytest.mark.parametrize("remaining,healthy", [(30, False), (60, False), (61, True)])
def test_check_near_expiry(cfg, remaining, healthy):
    write_outputs(cfg, rpst_for(exp=NOW + remaining))
    with mock.patch.object(m, "now", return_value=NOW):
        assert (m.check(cfg) is None) == healthy


def test_check_unreadable_token(cfg):
    write_outputs(cfg, "garbage")
    assert "no usable exp" in m.check(cfg)


def test_check_undecodable_file(cfg):
    (Path(cfg.output_dir) / "rpst").write_bytes(b"\xff\xfe")
    assert "cannot read" in m.check(cfg)


def test_now_is_wall_clock():
    assert abs(m.now() - time.time()) < 5


# ---- main ----------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch, cfg):
    """Environment equivalent to the cfg fixture."""
    monkeypatch.setenv("RPST_DOMAIN_URL", DOMAIN)
    monkeypatch.setenv("RPST_CLIENT_DIR", cfg.client_dir)
    monkeypatch.setenv("SERVICE_ACCOUNT_TOKEN_FILE", cfg.token_file)
    monkeypatch.setenv("OUTPUT_DIR", cfg.output_dir)
    return cfg


def test_main_check_ok(env, capsys):
    write_outputs(env, rpst_for(exp=NOW + 3600))
    with mock.patch.object(m, "now", return_value=NOW):
        assert m.main(["--check"]) == 0
    assert capsys.readouterr().out == ""


def test_main_check_unhealthy_prints_one_line(env, capsys):
    assert m.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("unhealthy: ") and out.count("\n") == 1


def test_main_once_succeeds_after_one_exchange(env, caplog):
    with caplog.at_level(logging.INFO), patch_exchange(ok_response(rpst_for())) as urlopen:
        assert m.main(["--once"]) == 0
    assert urlopen.call_count == 1
    assert "rpst issued" in caplog.text


def test_main_once_fails_without_retrying(env, caplog):
    with patch_exchange(http_error(401, "aud is missing or invalid")) as urlopen:
        assert m.main(["--once"]) == 1
    assert urlopen.call_count == 1
    assert "RPST exchange failed" in caplog.text


def test_main_once_fails_when_output_unwritable(env, caplog):
    with patch_exchange(ok_response(rpst_for())), mock.patch.object(m, "write_atomic", side_effect=OSError("read-only")):
        assert m.main(["--once"]) == 1
    assert "RPST exchange failed" in caplog.text


def test_main_bad_config(monkeypatch, caplog):
    monkeypatch.delenv("RPST_DOMAIN_URL", raising=False)
    assert m.main([]) == 1
    assert "Bad configuration" in caplog.text


def test_main_http_domain_rejected(monkeypatch):
    monkeypatch.setenv("RPST_DOMAIN_URL", "http://insecure.example")
    assert m.main([]) == 1
    assert m.main(["--check"]) == 1


def test_main_runs_until_signalled(env):
    handlers = {}
    with mock.patch.object(m.signal, "signal", side_effect=lambda s, h: handlers.__setitem__(s, h)), \
            patch_exchange(ok_response(rpst_for())):
        threading.Timer(0.3, lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)).start()
        assert m.main([]) == 0
    assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
    assert (Path(env.output_dir) / "rpst").exists()


def test_main_sigint_also_stops(env):
    handlers = {}
    with mock.patch.object(m.signal, "signal", side_effect=lambda s, h: handlers.__setitem__(s, h)), \
            patch_exchange(ok_response(rpst_for())):
        threading.Timer(0.3, lambda: handlers[signal.SIGINT](signal.SIGINT, None)).start()
        assert m.main([]) == 0
