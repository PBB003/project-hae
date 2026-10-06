"""Errores de la CLI y configuraciÃ³n de credenciales, sin archivos del usuario."""
import sys

import pytest

from client import hae_sync, youtrack_sync


@pytest.mark.parametrize("has_local_config", [False, True])
def test_configuration_has_no_default_api_key(monkeypatch, tmp_path, has_local_config):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    if has_local_config:
        (project_dir / "hae.json").write_text('{"project":{"id":"test","name":"Test"}}', encoding="utf-8")
    monkeypatch.setattr(hae_sync.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HAE_API_KEY", raising=False)
    assert hae_sync.load_or_create_config(str(project_dir))["api_key"] == ""


@pytest.mark.parametrize("command", ["youtrack", "sync"])
@pytest.mark.parametrize("succeeded", [False, True])
def test_youtrack_failure_controls_cli_exit(monkeypatch, tmp_path, command, succeeded):
    monkeypatch.setattr(sys, "argv", ["hae", command, "--path", str(tmp_path)])
    monkeypatch.setattr(hae_sync, "load_or_create_config", lambda _: {"project": {"id": "test"}})
    def scan(_,diagnostics=None):
        if diagnostics is not None: diagnostics.update(engine="tree-sitter", errors=[])
        return [], []
    monkeypatch.setattr(hae_sync, "scan_codebase", scan)
    monkeypatch.setattr(hae_sync, "request_json", lambda config,path: {"api_version":2} if path=="/api/health" else {"repositories":[]})
    monkeypatch.setattr(hae_sync, "sync_to_server", lambda *args: True)
    monkeypatch.setattr(youtrack_sync, "load_youtrack_config", lambda: {"yt_token": "test-token", "hae_project_id":"test"})
    monkeypatch.setattr(youtrack_sync, "sync_youtrack_to_hae", lambda *args: succeeded)
    # La CLI tambiÃ©n admite imports al ejecutarse directamente desde client/.
    monkeypatch.setitem(sys.modules, "youtrack_sync", youtrack_sync)
    if succeeded:
        hae_sync.main()
    else:
        with pytest.raises(SystemExit) as exc:
            hae_sync.main()
        assert exc.value.code == 1
