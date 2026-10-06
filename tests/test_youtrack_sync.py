"""Integración YouTrack -> HAE sin conexiones ni credenciales reales."""
import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from client import youtrack_sync


@pytest.fixture
def sync_config(monkeypatch):
    config = {"server_url": "https://hae.invalid", "api_key": "test-hae-key",
              "yt_base_url": "https://youtrack.invalid", "yt_token": "test-yt-token",
              "default_project": "TSO", "user_id": "dev", "hae_project_id": "test"}
    monkeypatch.setattr(youtrack_sync, "load_youtrack_config", lambda: config)
    # Cualquier petición de red no prevista falla inmediatamente.
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", Mock(side_effect=AssertionError("Red real prohibida")))
    return config


@pytest.fixture
def issue():
    return {"idReadable": "TSO-164", "id": "2-164", "updated": 200, "resolved": None, "summary": "Nómina certificada",
            "description": "Requisitos completos\n" * 100,
            "customFields": [{"name": "Assignee", "value": {"name": "Pedro", "id": "dev"}},
                             {"name": "State", "value": {"name": "In Progress"}}]}


@pytest.mark.parametrize("config", [None, {}, {"yt_token": ""}])
def test_missing_credentials_fail_without_network(monkeypatch, config):
    monkeypatch.setattr(youtrack_sync, "load_youtrack_config", lambda: config)
    network = Mock()
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    assert youtrack_sync.sync_youtrack_to_hae("test") is False
    network.assert_not_called()


def test_success_deduplicates_queries_and_preserves_assignment(sync_config, issue, monkeypatch):
    fetch = Mock(side_effect=[[issue], [issue, {**issue, "idReadable": "TSO-165", "id": "2-165", "customFields": issue["customFields"][1:]}]])
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", fetch)
    network = Mock(return_value=io.BytesIO(b'{}'))
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    assert youtrack_sync.sync_youtrack_to_hae("test") is True
    assert fetch.call_count == 2
    assert fetch.call_args_list[0].kwargs["query"] == "project: TSO for: me"
    assert fetch.call_args_list[1].kwargs["query"] == "project: TSO"
    payloads = json.loads(network.call_args.args[0].data)["tasks"]
    assert [payload["external_id"] for payload in payloads] == ["TSO-164", "TSO-165"]
    assert [payload["is_mine"] for payload in payloads] == [True, False]
    assert payloads[0]["description"] == issue["description"]
    assert payloads[0]["assignee"] == "Pedro"
    assert payloads[0]["status"] == "In Progress"
    assert network.call_args_list[0].args[0].get_header("X-hae-key") == sync_config["api_key"]


@pytest.mark.parametrize("error", [urllib.error.URLError("sin conexión"), TimeoutError("timeout")])
def test_youtrack_network_failure_returns_false(sync_config, monkeypatch, error, capsys):
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", Mock(side_effect=error))
    assert youtrack_sync.sync_youtrack_to_hae("test") is False
    assert "exitosa" not in capsys.readouterr().out
    youtrack_sync.urllib.request.urlopen.assert_not_called()


def test_partial_query_failure_is_reported(sync_config, issue, monkeypatch):
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", Mock(side_effect=[
        [issue], urllib.error.URLError("YouTrack no responde")]))
    network = Mock(return_value=io.BytesIO(b'{}'))
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    assert youtrack_sync.sync_youtrack_to_hae("test") is False
    assert network.call_count == 1


@pytest.mark.parametrize("error", [
    urllib.error.URLError("HAE no responde"), TimeoutError("timeout"),
    urllib.error.HTTPError("https://hae.invalid", 401, "Unauthorized", {}, io.BytesIO(b'{}')),
])
def test_hae_write_failure_returns_false(sync_config, issue, monkeypatch, error, capsys):
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", Mock(side_effect=[[issue], []]))
    network = Mock(side_effect=error)
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    assert youtrack_sync.sync_youtrack_to_hae("test") is False
    assert network.call_count == 1
    assert "exitosa" not in capsys.readouterr().out


def test_failed_atomic_snapshot_returns_false(sync_config, issue, monkeypatch):
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", Mock(side_effect=[
        [issue, {**issue, "idReadable": "TSO-165"}], []]))
    network = Mock(side_effect=[urllib.error.URLError("HAE no responde"), io.BytesIO(b'{}')])
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    assert youtrack_sync.sync_youtrack_to_hae("test") is False
    assert network.call_count == 1


def test_successful_empty_query_is_not_a_failure(sync_config, monkeypatch):
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", Mock(return_value=[]))
    network = Mock(return_value=io.BytesIO(b'{}'))
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    assert youtrack_sync.sync_youtrack_to_hae("test") is True
    assert json.loads(network.call_args.args[0].data)["tasks"] == []
    assert json.loads(network.call_args.args[0].data)["complete"] is True


def test_custom_query_uses_only_requested_filter(sync_config, monkeypatch):
    fetch = Mock(return_value=[])
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", fetch)
    assert youtrack_sync.sync_youtrack_to_hae("test", "project: TSO State: Review") is True
    assert fetch.call_count == 1
    assert fetch.call_args.kwargs["query"] == "project: TSO State: Review"


@pytest.mark.parametrize("batch", [{"error": "Unauthorized"}, [None], [{"summary": "Sin ID"}],
                                  [{"idReadable": "TSO-1", "summary": ""}]])
def test_malformed_youtrack_payloads_fail_without_writes(sync_config, monkeypatch, batch):
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", Mock(return_value=batch))
    assert youtrack_sync.sync_youtrack_to_hae("test") is False
    youtrack_sync.urllib.request.urlopen.assert_not_called()


def test_multi_assignee_field_is_supported(sync_config, issue, monkeypatch):
    issue["customFields"][0]["value"] = [{"name": "Pedro"}, {"name": "Ana"}]
    monkeypatch.setattr(youtrack_sync, "fetch_youtrack_issues", Mock(side_effect=[[issue], []]))
    network = Mock(return_value=io.BytesIO(b'{}'))
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    assert youtrack_sync.sync_youtrack_to_hae("test") is True
    assert json.loads(network.call_args.args[0].data)["tasks"][0]["assignee"] == "Pedro, Ana"


def test_missing_source_version_cannot_archive_tasks(sync_config, issue, monkeypatch):
    del issue['updated']
    monkeypatch.setattr(youtrack_sync, 'fetch_youtrack_issues', Mock(return_value=[issue]))
    assert youtrack_sync.sync_youtrack_to_hae('test') is False
    youtrack_sync.urllib.request.urlopen.assert_not_called()


def test_fetch_sends_query_token_and_full_description(sync_config, monkeypatch, issue):
    network = Mock(return_value=io.BytesIO(json.dumps([issue]).encode()))
    monkeypatch.setattr(youtrack_sync.urllib.request, "urlopen", network)
    result = youtrack_sync.fetch_youtrack_issues(sync_config["yt_base_url"], sync_config["yt_token"], "project: TSO for: me")
    assert result == [issue]
    request = network.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer test-yt-token"
    assert "description" in request.full_url
    assert "for%3A%20me" in request.full_url
    assert network.call_args.kwargs["timeout"] == 15
