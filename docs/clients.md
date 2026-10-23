# Connecting MCP clients

The server speaks MCP over **stdio** by default — the client spawns it as
a subprocess and owns its lifecycle. **Streamable HTTP** is the opt-in
alternative for clients that cannot spawn a subprocess.

## Claude Desktop (stdio)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mcp-agent-toolkit": {
      "command": "uv",
      "args": ["run", "mcp-toolkit-server"],
      "cwd": "C:/path/to/mcp-agent-toolkit-py",
      "env": {
        "SEARCH_PROVIDER": "none",
        "SANDBOX_ROOT": "C:/path/to/mcp-agent-toolkit-py/data/sandbox",
        "DB_PATH": "C:/path/to/mcp-agent-toolkit-py/data/demo.db"
      }
    }
  }
}
```

Points worth noting:

- `cwd` + an `env` block keep the sandbox and DB paths absolute — Claude
  Desktop does not share your shell's working directory or environment.
- The server needs no `OPENAI_API_KEY`: the model lives in the client.
- Seed the database first: `uv run python -m scripts.seed_db`.
- After `tools/list`, Claude sees four tools plus their annotations
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`),
  each tool's `outputSchema`, two resources (`reports://schema`,
  `sandbox://index`) and the `research_briefing` prompt.

## MCP Inspector (stdio)

```bash
npx @modelcontextprotocol/inspector uv run mcp-toolkit-server
```

The Inspector lists the same four tools, annotations, output schemas,
resources and prompt, and lets you call each one interactively.

## Streamable HTTP (opt-in)

```bash
# .env
MCP_TRANSPORT=streamable-http
MCP_HTTP_HOST=127.0.0.1
MCP_HTTP_PORT=8000
# MCP_HTTP_TOKEN=...   # required on any non-loopback bind
```

```bash
uv run mcp-toolkit-server
# endpoint: http://127.0.0.1:8000/mcp
```

Auth is a static bearer token — `Authorization: Bearer <MCP_HTTP_TOKEN>` —
compared in constant time. Binding to a non-loopback host without a
token fails at startup, before the socket opens.

**This transport is not hardened for the open Internet.** It is a
developer surface for Inspector-style remote clients. For anything
beyond localhost, put it behind TLS and a real identity layer — a static
bearer token is a shared secret, not authentication.

Python client:

```python
from mcp_toolkit.client.mcp_client import McpAgentClient

async with McpAgentClient.connect_streamable_http(
    "http://127.0.0.1:8000/mcp",
    token=None,  # or the configured token
) as client:
    tools = client.list_tools()
```

## Verified

- stdio: exercised end-to-end by `tests/integration/test_mcp_stdio.py`
  (real subprocess — discovery, calls, annotations, output schemas,
  `structuredContent`, resources, prompts).
- Streamable HTTP: exercised end-to-end by
  `tests/integration/test_streamable_http.py` — real `uvicorn` serve +
  the SDK's HTTP client, including 401s without/with a wrong token and
  the non-loopback-no-token startup refusal.
