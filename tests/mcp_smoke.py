"""Consulta MCP de lectura: HAE_API_KEY=... python tests/mcp_smoke.py [url_sse] --project ID."""
import argparse
import asyncio
import os

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


async def main(url, project):
    key = os.environ.get("HAE_API_KEY", "")
    if not key:
        raise SystemExit("Configura HAE_API_KEY en el entorno")
    async with sse_client(url, headers={"X-HAE-Key": key}, timeout=15, sse_read_timeout=30) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            result = await session.call_tool("hae_get_project_context", {"project_id": project})
            if result.is_error:
                raise SystemExit("La consulta MCP falló")
            for item in result.content:
                if hasattr(item, "text"):
                    print(item.text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke MCP/SSE de lectura")
    parser.add_argument("url", nargs="?", default="http://localhost:8000/sse")
    parser.add_argument("--project", default="project-hae")
    args = parser.parse_args()
    asyncio.run(main(args.url, args.project))
