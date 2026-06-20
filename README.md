# pyutcp-lab

A Python implementation of the Universal Tool Calling Protocol (UTCP). It has
three parts: a client that discovers tools and calls them across different
transports, an in-memory registry that indexes and caches those tools, and a
small agent orchestrator that can checkpoint a run and resume it later.

The idea behind UTCP is that a caller talks to each tool over its own native
transport (HTTP, SSE, a CLI process, and so on) rather than proxying everything
through one wrapper server.

## Status

Still early, but all five layers are in place: core models and variable
substitution, the transports, the registry, the client, and the agent.

## Layout

```
pyutcp_lab/
  core/        domain models, ${VAR} substitution, typed errors
  transports/  HTTP, SSE, CLI transports plus a connection pool
  registry/    in-memory repository, search index, result cache
  client/      UtcpClient: register, call, latency budget
  agent/       orchestrator, checkpointing, conversation memory
```

## Development

```bash
pip install -e ".[dev]"
pytest --cov=pyutcp_lab
```

Tests run fully offline. Every transport has an in-process fake, and anything
time-sensitive reads an injectable clock, so the suite is deterministic and
needs no network.

## Configuration

Instead of registering providers in code, you can describe them in a JSON or YAML
file and load the whole set at once. Every string runs through `${VAR}`
substitution, so URLs and credentials stay out of the committed file:

```json
{
  "variables": {"REGION": "eu"},
  "providers": [
    {
      "name": "weather",
      "transport": "http",
      "url": "https://${REGION}.weather.example/api",
      "auth": {"scheme": "bearer", "token": "${WEATHER_TOKEN}"}
    }
  ]
}
```

```python
from pyutcp_lab.core.config import load_config_file
from pyutcp_lab.client import client_from_config

config = load_config_file("providers.json", env_file=".env")
client = client_from_config(config, my_transport_factory)
```

## Command line

The package has a small CLI for inspecting configuration without reaching any
provider:

```bash
python -m pyutcp_lab validate providers.json
python -m pyutcp_lab list providers.json
python -m pyutcp_lab inspect providers.json weather
```
