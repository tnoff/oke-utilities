"""Tests for secret_age.main orchestration."""

from unittest.mock import patch

from secret_age.finding import Finding, Layer, Severity
from secret_age.main import main


def _mock_finding():
    return Finding(
        layer=Layer.OCI_IAM, identifier="x",
        last_rotated=None, age_days=10,
        severity=Severity.OK, rotation_command="cmd",
    )


def test_main_runs_all_readers_by_default(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with patch("secret_age.main.oci_reader.read_findings", return_value=[_mock_finding()]) as oci, \
         patch("secret_age.main.k8s_reader.read_findings", return_value=[]) as k8s, \
         patch("secret_age.main.layer1_reader.read_findings", return_value=[]) as layer1, \
         patch("secret_age.main.send_report") as send:
        rc = main()
    assert rc == 0
    oci.assert_called_once()
    k8s.assert_called_once()
    layer1.assert_called_once()
    send.assert_called_once()


def test_main_isolates_reader_failures(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with patch("secret_age.main.oci_reader.read_findings", side_effect=RuntimeError("oci boom")), \
         patch("secret_age.main.k8s_reader.read_findings", return_value=[_mock_finding()]) as k8s, \
         patch("secret_age.main.layer1_reader.read_findings", return_value=[]), \
         patch("secret_age.main.send_report"):
        rc = main()
    assert rc == 0  # main exits 0 even when readers fail
    k8s.assert_called_once()  # k8s still runs after oci failure


def test_main_isolates_k8s_and_layer1_failures_too(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with patch("secret_age.main.oci_reader.read_findings", return_value=[]), \
         patch("secret_age.main.k8s_reader.read_findings", side_effect=RuntimeError("k8s boom")), \
         patch("secret_age.main.layer1_reader.read_findings", side_effect=RuntimeError("ledger boom")), \
         patch("secret_age.main.send_report"):
        rc = main()
    assert rc == 0


def test_main_skips_disabled_readers(monkeypatch):
    monkeypatch.setenv("ENABLE_OCI_READER", "false")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with patch("secret_age.main.oci_reader.read_findings") as oci, \
         patch("secret_age.main.k8s_reader.read_findings", return_value=[]), \
         patch("secret_age.main.layer1_reader.read_findings", return_value=[]), \
         patch("secret_age.main.send_report"):
        main()
    oci.assert_not_called()


def test_main_installs_log_redaction(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with patch("secret_age.main.install_log_redaction") as install, \
         patch("secret_age.main.oci_reader.read_findings", return_value=[]), \
         patch("secret_age.main.k8s_reader.read_findings", return_value=[]), \
         patch("secret_age.main.layer1_reader.read_findings", return_value=[]), \
         patch("secret_age.main.send_report"):
        main()
    install.assert_called_once()
