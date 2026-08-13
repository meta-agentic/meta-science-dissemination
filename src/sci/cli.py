"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline
from .config import ConfigError, load, version
from .store import Store


def _report(message: str) -> None:
    print(message, flush=True)


def _open(args: argparse.Namespace) -> tuple[Store, object]:
    settings = load(Path(args.root) if args.root else None)
    return Store(settings.db_path), settings


def cmd_fetch(args: argparse.Namespace) -> int:
    store, settings = _open(args)
    with store:
        result = pipeline.run_fetch(store, settings, report=_report)
    print(f"\n{result.detail['new_total']} new item(s)")
    return 0 if result.ok else 1


def cmd_analyse(args: argparse.Namespace) -> int:
    store, settings = _open(args)
    with store:
        result = pipeline.run_analyse(store, settings, limit=args.limit, report=_report)
    detail = result.detail
    print(f"\n{detail['processed']} analysed — {detail['passed']} passed, "
          f"{detail['blocked']} sent to review")
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    store, settings = _open(args)
    with store:
        result = pipeline.run_draft(store, settings, limit=args.limit, report=_report)
    detail = result.detail
    print(f"\n{detail['written']} draft(s), {detail['review']} review note(s)")
    for error in detail["errors"]:
        print(f"  error: {error}", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    store, settings = _open(args)
    with store:
        results = pipeline.run_all(store, settings, limit=args.limit, report=_report)
    return 0 if all(r.ok for r in results) else 1


def cmd_status(args: argparse.Namespace) -> int:
    store, settings = _open(args)
    with store:
        stats = store.stats()
        print(f"sci {version()}")
        print(f"  database   {settings.db_path}")
        print(f"  drafts     {settings.drafts_dir}")
        print(f"  primary    {', '.join(s.id for s in settings.sources.primary)}")
        independent = [s.id for s in settings.sources.corroborators if s.is_independent]
        echo = [s.id for s in settings.sources.corroborators if not s.is_independent]
        print(f"  independent{'':<1} {', '.join(independent) or 'none'}")
        print(f"  echo-only  {', '.join(echo) or 'none'}")
        print()
        for key, value in stats.items():
            print(f"  {key:<10} {value}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store, settings = _open(args)
    with store:
        item = store.get_item(args.item_id)
        if not item:
            print(f"no such item: {args.item_id}", file=sys.stderr)
            return 1
        analysis = store.get_analysis(args.item_id)
        payload = {
            "item": {
                "id": item.id, "title": item.title, "summary": item.summary,
                "link": item.link, "published_at": item.published_at,
            },
            "analysis": analysis.to_dict() if analysis else None,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store, settings = _open(args)
    with store:
        for source in settings.sources.primary:
            for item, analysis in store.analysed_items(source.id)[: args.limit]:
                verdict = "PASS  " if analysis.passes else "review"
                print(f"{verdict} {analysis.hype.get('score', 0):>3}  {item.id}  {item.title[:60]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sci",
        description="Science news to verified Italian drafts.",
    )
    parser.add_argument("--root", help="project root (defaults to the installed package root)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="pull all configured feeds").set_defaults(func=cmd_fetch)

    analyse = sub.add_parser("analyse", help="bind, corroborate, extract claims, gate")
    analyse.add_argument("--limit", type=int, help="maximum items to analyse")
    analyse.set_defaults(func=cmd_analyse)

    drafting = sub.add_parser("draft", help="write Italian drafts for passing items")
    drafting.add_argument("--limit", type=int, help="maximum drafts to write")
    drafting.set_defaults(func=cmd_draft)

    full = sub.add_parser("run", help="fetch, analyse and draft in one pass")
    full.add_argument("--limit", type=int, help="maximum items per stage")
    full.set_defaults(func=cmd_run)

    sub.add_parser("status", help="configuration and database summary").set_defaults(func=cmd_status)

    show = sub.add_parser("show", help="dump the full evidence ledger for one item")
    show.add_argument("item_id")
    show.set_defaults(func=cmd_show)

    listing = sub.add_parser("list", help="list analysed items and their verdicts")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
