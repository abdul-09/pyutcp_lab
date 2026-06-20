"""Command-line interface for pyutcp-lab.

Run with ``python -m pyutcp_lab <command>``. The commands work offline: they
parse and inspect configuration without reaching any provider, which is what you
want when checking a config into version control or debugging substitution.

Commands:

* ``validate <config>`` parses a config file and reports how many providers it
  defines, failing with a non-zero exit code if the file is malformed.
* ``list <config>`` prints each provider's name and transport.
* ``inspect <config> <provider>`` prints the fully-resolved details of one
  provider, including the headers its auth contributes.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from .core.config import ConfigError, load_config_file
from .core.variables import VariableResolver


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyutcp_lab",
        description="Inspect UTCP provider configuration.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="parse a config and report")
    p_validate.add_argument("config", help="path to a JSON or YAML config")
    p_validate.add_argument("--env-file", help="optional .env file", default=None)

    p_list = sub.add_parser("list", help="list providers in a config")
    p_list.add_argument("config", help="path to a JSON or YAML config")
    p_list.add_argument("--env-file", help="optional .env file", default=None)

    p_inspect = sub.add_parser("inspect", help="show one provider's details")
    p_inspect.add_argument("config", help="path to a JSON or YAML config")
    p_inspect.add_argument("provider", help="provider name to inspect")
    p_inspect.add_argument("--env-file", help="optional .env file", default=None)

    return parser


def _cmd_validate(args: argparse.Namespace, out) -> int:
    config = load_config_file(args.config, env_file=args.env_file)
    count = len(config.providers)
    plural = "provider" if count == 1 else "providers"
    print(f"OK: {count} {plural} defined", file=out)
    return 0


def _cmd_list(args: argparse.Namespace, out) -> int:
    config = load_config_file(args.config, env_file=args.env_file)
    for pc in config.providers:
        print(f"{pc.provider.name}\t{pc.provider.transport.value}", file=out)
    return 0


def _cmd_inspect(args: argparse.Namespace, out) -> int:
    config = load_config_file(args.config, env_file=args.env_file)
    pc = config.get(args.provider)
    provider = pc.provider
    print(f"name:      {provider.name}", file=out)
    print(f"transport: {provider.transport.value}", file=out)
    if provider.url:
        print(f"url:       {provider.url}", file=out)
    if provider.command:
        print(f"command:   {' '.join(provider.command)}", file=out)
    auth_headers = pc.auth.headers(VariableResolver())
    if auth_headers:
        names = ", ".join(sorted(auth_headers))
        print(f"auth:      {type(pc.auth).__name__} (sets {names})", file=out)
    else:
        print(f"auth:      {type(pc.auth).__name__}", file=out)
    return 0


_COMMANDS = {
    "validate": _cmd_validate,
    "list": _cmd_list,
    "inspect": _cmd_inspect,
}


def main(argv: Optional[Sequence[str]] = None, *, out=sys.stdout, err=sys.stderr) -> int:
    """Entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS[args.command]
    try:
        return handler(args, out)
    except ConfigError as exc:
        print(f"error: {exc}", file=err)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
