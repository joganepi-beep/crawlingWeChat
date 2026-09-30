"""JSON CLI for bounded, local-only WeChat group context operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .config import load_config
from .dependency import WechatCli
from .errors import DependencyError, StorageError, ValidationError
from .tool import WxContextTool, default_data_dir


KNOWN_ERRORS = (ValidationError, DependencyError, StorageError)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wx-context")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("groups", help="list local group sessions")

    allow = commands.add_parser("allow", help="add an explicitly allowlisted group")
    allow.add_argument("session_id")
    allow.add_argument("display_name", nargs="?")
    allow.add_argument("--name", "--display-name", dest="display_name_option")

    collect = commands.add_parser("collect", help="collect a bounded date range")
    collect.add_argument("session_id")
    collect.add_argument("--start", required=True)
    collect.add_argument("--end", required=True)

    search = commands.add_parser("search", help="search stored message chunks")
    search.add_argument("terms", nargs="+")
    search.add_argument("--session-id")
    search.add_argument("--limit", type=int, default=50)

    context = commands.add_parser("context", help="return bounded chunk metadata")
    context.add_argument("collection_id")
    context.add_argument("--query", required=True)
    context.add_argument("--max-chunks", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one supported command and return a process-style exit code."""
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.data_dir)
        dependency = WechatCli(include_media=True)
        tool = WxContextTool(args.data_dir, config=config, dependency=dependency)

        if args.command == "groups":
            groups = [
                {
                    "session_id": group.session_id,
                    "display_name": group.display_name,
                    "is_group": group.is_group,
                }
                for group in tool.groups()
            ]
            _write_json({"groups": groups})
            return 0

        if args.command == "allow":
            if args.display_name is not None and args.display_name_option is not None:
                raise ValidationError("group name was provided more than once")
            display_name = (
                args.display_name
                if args.display_name is not None
                else args.display_name_option
            )
            group = tool.allow_group(args.session_id, display_name)
            _write_json(
                {
                    "session_id": group.session_id,
                    "display_name": group.display_name,
                    "allowed": True,
                }
            )
            return 0

        if args.command == "collect":
            manifest = tool.collect(args.session_id, args.start, args.end)
            _write_json(manifest.to_dict())
            return 0

        if args.command == "search":
            if len(args.terms) > 2 or (len(args.terms) == 2 and args.session_id):
                raise ValidationError("search accepts a query and optional session id")
            if len(args.terms) == 2:
                search_session_id, query = args.terms
            else:
                search_session_id, query = args.session_id, args.terms[0]
            results = tool.search(
                query, session_id=search_session_id, limit=args.limit
            )
            _write_json({"query": query, "limit": min(args.limit, 200), "results": results})
            return 0

        if args.command == "context":
            _write_json(
                tool.context(
                    args.collection_id,
                    args.query,
                    max_chunks=args.max_chunks,
                )
            )
            return 0

        raise ValidationError("unsupported command")
    except KNOWN_ERRORS as error:
        _write_error(error)
        return 2


def _write_json(payload: object) -> None:
    _ensure_utf8(sys.stdout)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_error(error: Exception) -> None:
    _ensure_utf8(sys.stderr)
    if isinstance(error, ValidationError):
        code, message = "validation_error", "invalid request"
    elif isinstance(error, DependencyError):
        code, message = "dependency_error", "dependency unavailable"
    else:
        code, message = "storage_error", "local storage unavailable"
    sys.stderr.write(
        json.dumps({"error": code, "message": message}, separators=(",", ":")) + "\n"
    )


def _ensure_utf8(stream: object) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="strict")
    except (OSError, ValueError):
        return


if __name__ == "__main__":  # pragma: no cover - exercised through console script
    raise SystemExit(main())
