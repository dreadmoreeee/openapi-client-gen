"""Command line: ``python -m openapi_client_gen spec.yaml -o client.py``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .generator import Generator
from .naming import pascal_case
from .spec import SpecError, load_spec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openapi-client-gen",
        description="Generate a typed, single-module httpx client from an OpenAPI 3.x spec.",
    )
    parser.add_argument("spec", help="OpenAPI 3.x file (.yaml, .yml or .json)")
    parser.add_argument("-o", "--output", help="file to write (default: print to stdout)")
    parser.add_argument("--class-name", help="client class name (default: from info.title)")
    parser.add_argument("--check", action="store_true",
                        help="do not write; exit 1 if OUTPUT is missing or out of date")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec = load_spec(args.spec)
        title = str((spec.get("info") or {}).get("title") or "Api")
        class_name = args.class_name or pascal_case(title, fallback="Api") + "Client"
        generator = Generator(spec, class_name)
        source = generator.generate()
    except (OSError, SpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for warning in generator.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if args.check:
        if not args.output:
            print("error: --check needs -o/--output", file=sys.stderr)
            return 2
        path = Path(args.output)
        current = path.read_bytes().decode("utf-8") if path.exists() else None
        if current != source:
            print(f"{path} is out of date; regenerate it", file=sys.stderr)
            return 1
        print(f"{path} is up to date")
        return 0
    if args.output:
        path = Path(args.output)
        path.write_bytes(source.encode("ascii"))
        count = source.count("\n")
        print(f"wrote {path} ({count} lines, class {class_name})")
    else:
        sys.stdout.write(source)
    return 0
