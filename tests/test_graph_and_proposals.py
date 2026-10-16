"""Pruebas integrales de las 4 mejoras: eficiencia de tokens, contexto guiado por ticket,
grafo relacional de símbolos en SQLite e indexación AST nativa de Python.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from client.scanner import scan_codebase
from server import mcp_server as mcp


@pytest.fixture
def rich_catalog(api_client, auth_headers):
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "project": {
            "id": "p",
            "name": "Proyecto Demo",
            "tech_stack": "React 19, TypeScript, FastAPI",
        },
        "mode": "snapshot",
        "repository": {
            "repo_id": "front",
            "branch": "main",
            "commit_sha": "1234567",
            "dirty": False,
            "scanned_at": now,
            "complete": True,
        },
        "components": [
            {
                "name": "PayrollTable",
                "file_path": "src/components/PayrollTable.tsx",
                "source_line": 10,
                "source_end_line": 85,
                "description": "Tabla de nómina certificada semanal",
                "contract": "export function PayrollTable(props: PayrollProps): JSX.Element",
                "type_resolution": "complete",
                "type_dependencies": [
                    {
                        "name": "PayrollProps",
                        "file_path": "src/types/payroll.ts",
                        "source_line": 1,
                        "source_end_line": 12,
                        "contract": "export interface PayrollProps { rows: PayrollRow[]; showTotals?: boolean; }",
                    },
                    {
                        "name": "PayrollRow",
                        "file_path": "src/types/payroll.ts",
                        "source_line": 14,
                        "source_end_line": 22,
                        "contract": "export interface PayrollRow { id: string; certified: boolean; }",
                    },
                ],
                "relations": [
                    {
                        "target_name": "StatusBadge",
                        "kind": "renders",
                        "target_file": "src/components/StatusBadge.tsx",
                        "line": 42,
                    },
                    {
                        "target_name": "useCertifiedPayroll",
                        "kind": "calls",
                        "target_file": "src/hooks/useCertifiedPayroll.ts",
                        "line": 18,
                    },
                ],
            },
            {
                "name": "StatusBadge",
                "file_path": "src/components/StatusBadge.tsx",
                "source_line": 4,
                "source_end_line": 20,
                "description": "Badge de estado reutilizable",
                "contract": "export function StatusBadge({ label }: { label: string }): JSX.Element",
                "type_resolution": "complete",
            },
        ],
        "utilities": [
            {
                "name": "useCertifiedPayroll",
                "file_path": "src/hooks/useCertifiedPayroll.ts",
                "source_line": 5,
                "source_end_line": 48,
                "type": "hook",
                "description": "Hook para filtrar y exportar nómina certificada",
                "contract": "export function useCertifiedPayroll(): { rows: PayrollRow[] }",
                "type_resolution": "complete",
                "type_dependencies": [
                    {
                        "name": "PayrollRow",
                        "file_path": "src/types/payroll.ts",
                        "source_line": 14,
                        "source_end_line": 22,
                        "contract": "export interface PayrollRow { id: string; certified: boolean; }",
                    }
                ],
                "relations": [
                    {
                        "target_name": "fetchPayrollApi",
                        "kind": "calls",
                        "target_file": "src/services/payrollApi.ts",
                        "line": 19,
                    }
                ],
            },
            {
                "name": "fetchPayrollApi",
                "file_path": "src/services/payrollApi.ts",
                "source_line": 1,
                "source_end_line": 30,
                "type": "service",
                "description": "Servicio HTTP para nómina certificada",
                "contract": "export async function fetchPayrollApi(): Promise<PayrollRow[]>",
                "type_resolution": "complete",
            },
        ],
    }
    api_client.post("/api/sync", headers=auth_headers, json=payload).raise_for_status()
    return api_client, auth_headers, payload


def test_source_end_line_and_exclude_types_reduce_tokens_with_distinct_etags(rich_catalog):
    full = json.loads(
        asyncio.run(
            mcp.hae_get_symbol("p", "PayrollTable", "src/components/PayrollTable.tsx")
        )
    )
    row = full["results"][0]
    assert row["source_line"] == 10 and row["source_end_line"] == 85
    assert {t["name"] for t in row["types"]} == {"PayrollProps", "PayrollRow"}
    assert row["types"][0]["source_end_line"] in (12, 22)

    filtered = json.loads(
        asyncio.run(
            mcp.hae_get_symbol(
                "p",
                "PayrollTable",
                "src/components/PayrollTable.tsx",
                exclude_types=["PayrollRow"],
                if_none_match=full["etag"],
            )
        )
    )
    assert not filtered.get("not_modified")
    assert filtered["etag"] != full["etag"]
    assert [t["name"] for t in filtered["results"][0]["types"]] == ["PayrollProps"]
    assert filtered["results"][0]["types_excluded"] == 1

    cached = json.loads(
        asyncio.run(
            mcp.hae_get_symbol(
                "p",
                "PayrollTable",
                "src/components/PayrollTable.tsx",
                exclude_types=["PayrollRow"],
                if_none_match=filtered["etag"],
            )
        )
    )
    assert cached == {"not_modified": True, "etag": filtered["etag"]}


def test_query_knowledge_uses_budgeted_response_and_location_by_default(rich_catalog):
    res = json.loads(
        asyncio.run(mcp.hae_query_knowledge("p", "nómina certificada", limit=5))
    )
    assert res["level"] == "location"
    assert res["match"] == "candidates"
    assert len(res["results"]) >= 2
    first = res["results"][0]
    assert "source_line" in first and "source_end_line" in first
    assert "kind" in first and "score" in first
    assert "contract" not in first

    cached = json.loads(
        asyncio.run(
            mcp.hae_query_knowledge(
                "p", "nómina certificada", limit=5, if_none_match=res["etag"]
            )
        )
    )
    assert cached == {"not_modified": True, "etag": res["etag"]}

    exact = json.loads(
        asyncio.run(
            mcp.hae_query_knowledge(
                "p",
                "PayrollTable",
                response_level="contract",
                exclude_types=["PayrollRow"],
            )
        )
    )
    assert exact["level"] == "contract" and exact["match"] == "exact"
    assert [t["name"] for t in exact["results"][0]["types"]] == ["PayrollProps"]


def test_dynamic_ticket_guided_context_auto_detects_branch_ticket_and_candidates(rich_catalog):
    client, headers, _ = rich_catalog
    client.post(
        "/api/projects/p/tasks",
        headers=headers,
        json={
            "external_id": "TSO-164",
            "title": "Filtro en tabla de nómina certificada",
            "status": "In Progress",
            "description": "Agregar checkbox en PayrollTable usando useCertifiedPayroll para filtrar filas.",
            "is_mine": True,
        },
    ).raise_for_status()
    client.post(
        "/api/projects/p/sessions",
        headers=headers,
        json={
            "summary": "Sesión previa en main",
            "branch": "main",
            "ticket_id": "TSO-100",
            "agent": "codex",
            "commit_sha": "1234567",
        },
    ).raise_for_status()

    # Branch includes TSO-164 without explicit ticket_id parameter
    context_text = asyncio.run(
        mcp.hae_get_project_context(
            "p", branch="TSO-164/checkbox-certified-payroll"
        )
    )
    assert "Foco de Ticket Activo - TSO-164 (detectado desde rama)" in context_text
    assert "PayrollTable" in context_text
    assert "src/components/PayrollTable.tsx:L10-L85" in context_text
    # Fallback session from general scope when TSO-164 has no session yet
    assert "Sesión previa en main" in context_text


def test_symbol_graph_outgoing_incoming_and_multi_hop_impact(rich_catalog):
    client, headers, _ = rich_catalog

    # Outgoing from PayrollTable (depth=2 reaches fetchPayrollApi via useCertifiedPayroll)
    out_graph = json.loads(
        asyncio.run(
            mcp.hae_get_symbol_graph(
                "p", "PayrollTable", direction="outgoing", depth=2
            )
        )
    )
    assert out_graph["match"] == "exact"
    targets = {e["target"]: e for e in out_graph["edges"]}
    assert targets["StatusBadge"]["kind"] == "renders"
    assert targets["useCertifiedPayroll"]["kind"] == "calls"
    assert targets["PayrollProps"]["kind"] == "uses_type"
    assert targets["fetchPayrollApi"]["distance"] == 2

    # Incoming impact (blast radius) on fetchPayrollApi (depth=2 reaches PayrollTable)
    in_graph = json.loads(
        asyncio.run(
            mcp.hae_get_symbol_graph(
                "p", "fetchPayrollApi", direction="incoming", depth=2
            )
        )
    )
    sources = {e["source"]: e for e in in_graph["edges"]}
    assert "useCertifiedPayroll" in sources and sources["useCertifiedPayroll"]["distance"] == 1
    assert "PayrollTable" in sources and sources["PayrollTable"]["distance"] == 2

    # REST endpoint & ETag validation
    rest = client.get(
        "/api/projects/p/symbols/PayrollTable/graph?direction=both&depth=2",
        headers=headers,
    )
    assert rest.status_code == 200
    rest_json = rest.json()
    assert rest_json["summary"]["outgoing_count"] >= 4
    not_mod = client.get(
        f"/api/projects/p/symbols/PayrollTable/graph?direction=both&depth=2&if_none_match={rest_json['etag']}",
        headers=headers,
    )
    assert not_mod.status_code == 200 and not_mod.json()["not_modified"] is True


def test_python_ast_scanner_and_ts_relations_extraction(tmp_path: Path):
    # Create mini mixed Python + TS workspace
    (tmp_path / "models.py").write_text(
        "from pydantic import BaseModel\n\n"
        "class ItemPayload(BaseModel):\n"
        "    name: str\n"
        "    count: int = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        "from models import ItemPayload\n\n"
        "def process_item(payload: ItemPayload) -> bool:\n"
        "    \"\"\"Procesa un item validado.\"\"\"\n"
        "    return validate_name(payload.name)\n\n"
        "def validate_name(value: str) -> bool:\n"
        "    return bool(value.strip())\n",
        encoding="utf-8",
    )
    (tmp_path / "View.tsx").write_text(
        "import React from 'react';\n"
        "export function ChildBadge() { return <span />; }\n"
        "export function ParentPanel() {\n"
        "  const ok = helperFormat('x');\n"
        "  return <div><ChildBadge />{ok}</div>;\n"
        "}\n"
        "export function helperFormat(v: string): string { return v.trim(); }\n",
        encoding="utf-8",
    )

    diagnostics = {}
    components, utilities = scan_codebase(tmp_path, diagnostics=diagnostics)
    assert diagnostics["errors"] == []

    by_name = {u["name"]: u for u in utilities}
    assert "ItemPayload" in by_name
    assert by_name["ItemPayload"]["type"] == "type"
    assert "process_item" in by_name
    proc = by_name["process_item"]
    assert proc["source_line"] == 3 and proc["source_end_line"] == 5
    assert any(d["name"] == "ItemPayload" for d in proc.get("type_dependencies", []))
    assert any(r["target_name"] == "validate_name" and r["kind"] == "calls" for r in proc.get("relations", []))

    comp_by_name = {c["name"]: c for c in components}
    parent = comp_by_name["ParentPanel"]
    assert any(r["target_name"] == "ChildBadge" and r["kind"] == "renders" for r in parent.get("relations", []))
    assert any(r["target_name"] == "helperFormat" and r["kind"] == "calls" for r in parent.get("relations", []))


def test_project_hae_can_index_and_query_its_own_python_graph(api_client, auth_headers):
    repo_root = Path(__file__).resolve().parent.parent
    diagnostics = {}
    components, utilities = scan_codebase(repo_root, diagnostics=diagnostics)
    assert diagnostics["errors"] == []
    assert len(utilities) >= 40

    payload = {
        "project": {"id": "project-hae-self", "name": "Project HAE", "tech_stack": "Python, FastAPI, MCP"},
        "mode": "snapshot",
        "repository": {
            "repo_id": "project-hae",
            "branch": "main",
            "commit_sha": "abc1234",
            "dirty": False,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "complete": True,
        },
        "components": components,
        "utilities": utilities,
    }
    resp = api_client.post("/api/sync", headers=auth_headers, json=payload)
    assert resp.status_code == 200, resp.text

    graph = json.loads(
        asyncio.run(
            mcp.hae_get_symbol_graph(
                "project-hae-self", "hae_get_symbol_graph", direction="outgoing", depth=1
            )
        )
    )
    assert graph["match"] == "exact"
    assert any(e["target"] == "RegistryService.get_symbol_graph" and e["kind"] == "calls" for e in graph["edges"])

    # Budget trimming on graph payload
    budgeted = json.loads(
        asyncio.run(
            mcp.hae_get_symbol_graph(
                "project-hae-self", "RegistryService.sync_catalog", direction="both", depth=2, max_tokens=512
            )
        )
    )
    assert len(json.dumps(budgeted, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 512

