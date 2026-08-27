"""CLI entrypoint — `pos-backend serve` (and friends)."""

from __future__ import annotations

import argparse
from typing import Any

import uvicorn

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)


def _cmd_serve(args: argparse.Namespace) -> int:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
        access_log=False,  # we use our own logging
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pos-backend")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI app via uvicorn")
    serve.add_argument("--reload", action="store_true", help="Enable hot-reload")
    serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    func: Any = args.func
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
