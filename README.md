# pyutcp-lab

A Python implementation of the Universal Tool Calling Protocol (UTCP): a client
for discovering and calling tools across many transports, an in-memory tool
registry with search and caching, and a small agent orchestrator with
checkpointing.

UTCP lets a caller discover "tools" (APIs) and invoke them over their native
transport — HTTP, SSE, CLI, and others — instead of routing every call through a
wrapper server.

## Status

Early. The core layer (domain models, variable substitution, error hierarchy) is
in place. Transports, registry, and agent layers are landing incrementally.

## Layout

```
pyutcp_lab/
  core/        domain models, ${VAR} substitution, typed errors
  transports/  HTTP / SSE / CLI transports + connection pool   (done)
  registry/    in-memory repository, search index, result cache (done)
  client/      UtcpClient: register, call, latency budget           (done)
  agent/       orchestrator, checkpointing, conversation memory (orchestrator + checkpoint done)
```

## Development

```bash
pip install -e ".[dev]"
pytest --cov=pyutcp_lab
```
