"""Identidad y metadatos Git sin ejecutar comandos aportados por documentos."""
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def git_metadata(path):
    git = shutil.which("git")
    if not git:
        return {"branch":"", "commit_sha":"", "dirty":False}
    def read(*args):
        try:
            result = subprocess.run([git,"-C",str(path),*args],capture_output=True,text=True,timeout=10)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError("No se pudieron verificar los metadatos Git") from exc
        return result.stdout.strip() if result.returncode == 0 else ""
    return {"branch":read("branch","--show-current"),"commit_sha":read("rev-parse","HEAD"),
            "dirty":bool(read("status","--porcelain"))}


def repository_roots(root_dir, config):
    root = Path(root_dir).resolve()
    specs = config.get("repositories")
    if not specs:
        nested = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.') and (p/".git").exists()]
        specs = [{"id":p.name,"path":p.name} for p in nested] if nested and not (root/".git").exists() else [{"id":config["project"]["id"],"path":"."}]
    if not isinstance(specs,list) or any(not isinstance(spec,dict) or not isinstance(spec.get("path"),str) or not isinstance(spec.get("id"),str) for spec in specs):
        raise ValueError("repositories debe ser una lista con id y path de texto")
    result, ids = [], set()
    for spec in specs:
        path = (root/spec["path"]).resolve()
        repo_id = spec.get("id")
        if not repo_id or repo_id in ids or not path.is_relative_to(root) or not path.is_dir() or (root/spec["path"]).is_symlink():
            raise ValueError("Los repositorios requieren IDs únicos y rutas existentes dentro del workspace")
        ids.add(repo_id)
        if any(path.is_relative_to(other) or other.is_relative_to(path) for _,other in result):
            raise ValueError("Los repositorios declarados no pueden solaparse")
        result.append((repo_id,path))
    return result


def manifest(repo_id, path, revision=0, allow_empty=False):
    return {"repo_id":repo_id,**git_metadata(path),"scanned_at":datetime.now(timezone.utc).isoformat(),
            "expected_revision":revision,"complete":True,"allow_empty":allow_empty}


def verify_agent_identity(root_dir, project_id):
    import re
    path = Path(root_dir)/"AGENTS.md"
    if path.exists():
        ids = set(re.findall(r'project_id="([^"]+)"',path.read_text(encoding='utf-8-sig')))
        if ids and ids != {project_id}:
            raise ValueError(f"AGENTS.md corresponde a {sorted(ids)}; hae.json corresponde a {project_id}")
