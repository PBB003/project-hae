import os
import sys
import json
import re
import urllib.request
from pathlib import Path

# Soporte UTF-8 en consolas Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly"
]

CREDENTIALS_FILE = Path.home() / ".hae_google_credentials.json"
TOKEN_FILE = Path.home() / ".hae_google_token.json"

def get_google_creds():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        
        if not creds:
            if not CREDENTIALS_FILE.exists():
                print("=" * 60)
                print("[!] Para conectar Google Drive automáticamente:")
                print(f"    1. Descarga el archivo 'credentials.json' (OAuth Client ID) desde Google Cloud Console.")
                print(f"    2. Guárdalo en: {CREDENTIALS_FILE}")
                print("=" * 60)
                return None
            
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    
    return creds

def extract_text_from_doc(doc_content: dict) -> str:
    """Extrae todo el texto plano de un documento de Google Docs."""
    text_pieces = []
    body = doc_content.get("body", {})
    elements = list(reversed(body.get("content", [])))
    while elements:
        element = elements.pop()
        if "paragraph" in element:
            for p_elem in element["paragraph"].get("elements", []):
                if "textRun" in p_elem:
                    text_pieces.append(p_elem["textRun"].get("content", ""))
        if "table" in element:
            nested = [content for row in element["table"].get("tableRows", [])
                      for cell in row.get("tableCells", []) for content in cell.get("content", [])]
            elements.extend(reversed(nested))
    return "".join(text_pieces)

def parse_gemini_notes(full_text: str):
    """Extrae el resumen y acuerdos clave de las notas generadas por Gemini."""
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]
    summary_parts = []
    action_parts = []
    
    in_summary = False
    in_actions = False

    for line in lines:
        lower = line.lower().strip("# :•-*")
        if lower in ("resumen", "resumen ejecutivo", "summary"):
            in_summary = True
            in_actions = False
            continue
        elif lower in ("elementos de acción", "acuerdos", "acuerdos clave", "action items", "próximos pasos", "proximos pasos", "next steps", "acciones a seguir", "tareas pendientes"):
            in_actions = True
            in_summary = False
            continue
        elif lower in ("transcripción", "asistentes", "invitados", "archivos adjuntos", "transcript", "registro de chat", "detalles de la reunión", "notas adicionales"):
            in_summary = False
            in_actions = False

        if in_summary:
            summary_parts.append(line)
        elif in_actions:
            action_parts.append(line)

    summary = "\n".join(summary_parts) if summary_parts else (lines[2] if len(lines) > 2 else "Notas de la reunión")
    action_items = "\n".join(action_parts) if action_parts else None

    return summary, action_items

def _sync_google_meetings_to_hae(project_id: str):
    global_config_path = Path.home() / ".hae.json"
    server_url = "https://129.213.16.122.sslip.io"
    api_key = ""
    if global_config_path.exists():
        try:
            data = json.loads(global_config_path.read_text(encoding="utf-8-sig"))
            server_url = data.get("server_url", server_url).rstrip("/")
            api_key = data.get("api_key", "")
        except Exception:
            pass

    try:
        creds = get_google_creds()
    except Exception as exc:
        print(f"[Error] No se pudo autenticar Google: {type(exc).__name__}")
        return False
    if not creds:
        return False

    from googleapiclient.discovery import build
    drive_service = build("drive", "v3", credentials=creds)
    docs_service = build("docs", "v1", credentials=creds)

    print(f"🔍 Buscando documentos de Daily para '{project_id}' en Google Drive...")
    query = f"name contains 'Daily' and mimeType = 'application/vnd.google-apps.document' and trashed = false"
    files, page_token = [], None
    while True:
        results = drive_service.files().list(q=query, pageSize=100, pageToken=page_token,
            orderBy="createdTime desc", fields="nextPageToken,files(id, name, createdTime, webViewLink)").execute()
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    files = [file for file in files if project_id.lower() in file["name"].lower()]
    if not files:
        print(f"[!] No se encontraron documentos Daily para '{project_id}'.")
        return False

    synced = 0
    failed = False
    for file in files:
        file_id = file["id"]
        title = file["name"]
        link = file.get("webViewLink", "")
        
        # Filtrar si corresponde al proyecto (ej: 'tekniek' en el título o coincide el ID)
        if project_id.lower() not in title.lower():
            continue

        # Extraer fecha del título o metadata (YYYY/MM/DD o YYYY-MM-DD)
        date_match = re.search(r'(\d{4}[/-]\d{2}[/-]\d{2})', title)
        m_date = date_match.group(1).replace('/', '-') if date_match else file.get("createdTime", "")[:10]

        try:
            doc = docs_service.documents().get(documentId=file_id).execute()
            doc_text = extract_text_from_doc(doc)
            summary, actions = parse_gemini_notes(doc_text)

            payload = {
                "title": title,
                "meeting_date": m_date,
                "summary": summary,
                "action_items": actions,
                "raw_notes": doc_text,
                "source_id": file_id,
                "source_url": link
            }

            endpoint = f"{server_url}/api/projects/{project_id}/meetings"
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_data, method="POST")
            req.add_header("Content-Type", "application/json")
            if api_key:
                req.add_header("X-HAE-Key", api_key)

            with urllib.request.urlopen(req, timeout=15) as resp:
                synced += 1
                print(f"  ✔ Sincronizado: {title} ({m_date})")
        except Exception as e:
            failed = True
            print(f"  [!] Error procesando '{title}': {type(e).__name__}")

    print("=" * 60)
    print(f"✨ Sincronización con Google Drive completada ({synced} reuniones guardadas)!")
    print("=" * 60)
    return not failed


def sync_google_meetings_to_hae(project_id: str = "tekniek"):
    try:
        return _sync_google_meetings_to_hae(project_id)
    except Exception as exc:
        print(f"[Error] Sincronización Google incompleta: {type(exc).__name__}")
        return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sincronizador Google Drive / Gemini Notes -> HAE")
    parser.add_argument("--project", default="tekniek", help="ID del proyecto HAE")
    args = parser.parse_args()
    raise SystemExit(0 if sync_google_meetings_to_hae(args.project) else 1)
