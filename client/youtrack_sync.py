import os
import sys
import json
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

# Soporte UTF-8 en consolas Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def load_youtrack_config():
    global_config_path = Path.home() / ".hae.json"
    if global_config_path.exists():
        try:
            data = json.loads(global_config_path.read_text(encoding="utf-8-sig"))
            yt = data.get("youtrack", {})
            return {
                "server_url": data.get("server_url", "https://129.213.16.122.sslip.io"),
                "api_key": data.get("api_key", ""),
                "yt_base_url": yt.get("base_url", "https://intellisys.youtrack.cloud").rstrip("/"),
                "yt_token": yt.get("token", ""),
                "default_project": yt.get("default_project", "TSO"),
                "user_id": yt.get("user_id"),
                "hae_project_id": yt.get("hae_project_id", "tekniek")
            }
        except Exception:
            pass
    return None

def fetch_youtrack_issues(yt_base_url: str, yt_token: str, query: str = "project: TSO", top: int = 20):
    fields = "id,idReadable,summary,description,resolved,updated,customFields(name,value(id,name,login,isResolved))"
    issues, skip = [], 0
    top = max(1,min(int(top),100))
    while True:
        endpoint = f"{yt_base_url}/api/issues?query={urllib.parse.quote(query)}&fields={fields}&$top={top}&$skip={skip}"
        req = urllib.request.Request(endpoint,headers={"Authorization":f"Bearer {yt_token}","Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=15) as response:
            batch = json.loads(response.read().decode("utf-8"))
        if not isinstance(batch,list):
            raise ValueError("YouTrack no devolvió una lista")
        if any(not isinstance(issue,dict) or type(issue.get("updated")) is not int for issue in batch):
            raise ValueError("YouTrack devolvió tickets sin una fecha updated verificable")
        issues.extend(batch)
        if len(batch) < top:
            return issues
        skip += top
        if skip > 100000:
            raise ValueError("Límite de paginación alcanzado; no se aplicará una sincronización completa")


def issue_to_task(issue, user_id, base_url, fallback_mine=False):
    if not isinstance(issue,dict) or not isinstance(issue.get("idReadable"),str) or not issue["idReadable"].strip() or not isinstance(issue.get("summary"),str) or not issue["summary"].strip():
        raise ValueError("Ticket inválido: ID y título obligatorios")
    assignee, assignee_ids, status, known_assignment = None, [], "Open", False
    for field in issue.get("customFields") or []:
        name, value = field.get("name"), field.get("value")
        if name in ("Assignee","Asignado a"):
            users = value if isinstance(value,list) else ([value] if value else [])
            assignee = ", ".join(user["name"] for user in users if user.get("name")) or None
            assignee_ids = [user["id"] for user in users if user.get("id")]
            known_assignment = not users or bool(assignee_ids)
        elif name in ("Stage","State","Estado") and value:
            status = value.get("name","Open")
    return {"external_id":issue["idReadable"],"title":issue["summary"],"status":status,
            "description":issue.get("description"),"assignee":assignee,
            "url":f"{base_url}/issue/{issue['idReadable']}",
            "is_mine":user_id in assignee_ids if known_assignment else fallback_mine,
            "assignee_ids":assignee_ids,"source_id":issue.get("id"),
            "resolved_at":issue.get("resolved"),"source_updated_at":issue.get("updated")}


def sync_youtrack_to_hae(project_id: str = "tekniek", custom_query: str = None):
    cfg = load_youtrack_config()
    if not cfg or not cfg.get("yt_token"):
        print("[Error] No se encontraron credenciales de YouTrack en ~/.hae.json")
        return False
    if project_id != cfg.get("hae_project_id", "tekniek"):
        print("[Error] El proyecto HAE no coincide con el mapeo YouTrack; corrige youtrack.hae_project_id")
        return False
    observed_at = datetime.now(timezone.utc).isoformat()
    user_id = cfg.get("user_id")
    if not user_id:
        try:
            req = urllib.request.Request(cfg["yt_base_url"]+"/api/users/me?fields=id",headers={"Authorization":f"Bearer {cfg['yt_token']}"})
            with urllib.request.urlopen(req,timeout=15) as response:
                user_id = json.loads(response.read().decode())["id"]
        except Exception as exc:
            print(f"[Error] No se pudo identificar al usuario YouTrack: {type(exc).__name__}")
            return False
    project_code = cfg.get("default_project","TSO")
    queries = [custom_query] if custom_query else [f"project: {project_code} for: me",f"project: {project_code}"]
    my_ids, all_issues, had_errors = set(), {}, False
    for index, query in enumerate(queries):
        try:
            batch = fetch_youtrack_issues(cfg["yt_base_url"],cfg["yt_token"],query=query,top=20)
            if not isinstance(batch,list):
                raise ValueError("Respuesta de YouTrack inválida")
            # Validar la página completa antes de incorporarla al snapshot.
            for item in batch:
                issue_to_task(item,user_id,cfg["yt_base_url"])
                if type(item.get("updated")) is not int:
                    raise ValueError("Ticket sin versión updated verificable")
                if item["idReadable"].rsplit('-',1)[0] != project_code:
                    raise ValueError("La consulta devolvió tickets de otro proyecto YouTrack")
            for item in batch:
                iid = item["idReadable"]
                if (index == 0 and not custom_query) or "for: me" in query:
                    my_ids.add(iid)
                old = all_issues.get(iid)
                if old is None or (item.get("updated") or 0) >= (old.get("updated") or 0):
                    all_issues[iid] = item
        except Exception as exc:
            had_errors = True
            print(f"[Error] Consulta incompleta: {type(exc).__name__}")
    if not all_issues and (had_errors or custom_query):
        return not had_errors
    payload = {"tasks":[issue_to_task(item,user_id,cfg["yt_base_url"],iid in my_ids) for iid,item in all_issues.items()],
               "viewer_id":user_id,"complete":not custom_query and not had_errors,"observed_at":observed_at}
    endpoint = f"{cfg['server_url'].rstrip('/')}/api/projects/{project_id}/tasks/sync"
    req = urllib.request.Request(endpoint,data=json.dumps(payload).encode("utf-8"),method="POST",
        headers={"Content-Type":"application/json","X-HAE-Key":cfg["api_key"]})
    try:
        with urllib.request.urlopen(req,timeout=30):
            pass
    except Exception as exc:
        print(f"[Error] No se pudo guardar el snapshot de tickets: {type(exc).__name__}")
        return False
    print(f"Sincronización {'incompleta' if had_errors else 'exitosa'}: {len(payload['tasks'])} tickets; usuario {user_id}.")
    return not had_errors


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sincronizador YouTrack -> HAE")
    parser.add_argument("--project",default="tekniek")
    parser.add_argument("--query",default=None)
    args = parser.parse_args()
    raise SystemExit(0 if sync_youtrack_to_hae(args.project,args.query) else 1)
