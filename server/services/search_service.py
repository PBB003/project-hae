"""Ranking local explicable por nombres, términos y conceptos español/inglés.

No descarga modelos, no usa embeddings entrenados y no envía código a terceros.
"""
import math
import re
import unicodedata
from collections import Counter

CONCEPTS = {
    "project": {"proyecto", "proyectos", "project", "projects"},
    "create": {"crear", "creacion", "create", "creation", "add", "agregar", "nuevo"},
    "checkbox": {"checkbox", "casilla", "check", "seleccion", "selector"},
    "payroll": {"nomina", "payroll", "certified", "certificada"},
    "table": {"tabla", "table", "grid", "datagrid"},
    "pagination": {"paginada", "paginacion", "pagination", "paginated", "paginate"},
    "date": {"fecha", "fechas", "date", "dates", "calendar", "calendario", "datepicker"},
    "filter": {"filtro", "filtrar", "filter", "filters", "search", "buscar", "busqueda"},
    "upload": {"subir", "subida", "cargar", "upload", "uploads"},
    "invoice": {"factura", "facturas", "invoice", "invoices"},
    "form": {"formulario", "form", "forms"},
    "auth": {"autenticacion", "authentication", "auth", "login", "sesion"},
    "folder": {"carpeta", "carpetas", "folder", "folders", "documental"},
}
STOPWORDS = {"de", "del", "la", "el", "los", "las", "un", "una", "con", "para", "en", "y", "necesito", "quiero", "the", "a", "an", "with", "for", "and", "to"}


def tokens(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return [x for x in re.findall(r"[a-z0-9]+", text) if x not in STOPWORDS]


def concept_vector(words):
    counts = Counter(words)
    return {concept: sum(counts[word] for word in aliases) for concept, aliases in CONCEPTS.items()}


def rank(items, query, fields, limit):
    if not query or not query.strip():
        return sorted(items, key=lambda item: (item["name"], item.get("repo_id", ""), item.get("branch", "")))[:limit]
    query = query.strip()
    words = tokens(query)
    qv = concept_vector(words)
    scored = []
    for item in items:
        document = " ".join(str(item.get(field) or "") for field in fields)
        exact = all(term in document.lower() for term in query.lower().split())
        if any(c in query for c in ("%", "_", "\\")):
            if not exact:
                continue
            score, reason = 100.0, "literal"
        else:
            doc_words = set(tokens(document))
            dv = concept_vector(doc_words)
            dot = sum(qv[key] * dv[key] for key in CONCEPTS)
            norm = math.sqrt(sum(x*x for x in qv.values()) * sum(x*x for x in dv.values()))
            cosine = dot / norm if norm else 0
            overlap = len(set(words) & doc_words) / max(1, len(set(words)))
            name_match = query.casefold() == item["name"].casefold()
            if not (exact or name_match or overlap > 0 or cosine > 0):
                continue
            score = 100 * name_match + 40 * exact + 20 * overlap + 20 * cosine
            reason = "nombre exacto" if name_match else ("términos" if exact else "conceptos/terminos")
        scored.append({**item, "score": round(score, 3), "match_reason": reason})
    return sorted(scored, key=lambda item: (-item["score"], item["name"]))[:limit]
