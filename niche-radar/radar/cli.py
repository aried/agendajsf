"""Command line: python -m radar <run|check|report|status>."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import config, pipeline, report
from .http import Http, SourceError
from .sources.youtube import YouTube
from .store import Store


def _youtube(http, store, cfg):
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    return YouTube(http, key, store, cfg.youtube) if key else None


def cmd_run(args, cfg, http, store) -> int:
    yt = None if args.skip_youtube else _youtube(http, store, cfg)
    summary = pipeline.run(http, store, cfg, yt, max_candidates=args.max_candidates)
    path = report.write(store, cfg, summary["run_id"])
    print(f"sinais: {summary['signals']} · candidatos: {summary['candidates']} · YouTube: {summary['youtube']}")
    print(f"relatório: {path}")
    return 0


def cmd_check(args, cfg, http, store) -> int:
    if args.market not in cfg.markets:
        print(f"mercado desconhecido: {args.market} (use {', '.join(cfg.markets)})", file=sys.stderr)
        return 2
    market = cfg.markets[args.market]
    run_id = store.start_run("check")
    today = datetime.now(timezone.utc).date()
    data = pipeline.check_term(http, store, market, args.term, today)
    store.add_check(run_id, market.key, args.term, "check", data)
    summary = {"run_id": run_id, "signals": 0, "candidates": 1, "youtube": "não pedido"}
    yt = _youtube(http, store, cfg) if args.youtube else None
    if args.youtube and yt is None:
        summary["youtube"] = "sem chave"
    elif yt is not None:
        try:
            metrics = pipeline.measure_query(yt, store, cfg, run_id, market, args.term, data.get("momentum"))
            data["youtube"] = {k: v for k, v in metrics.items() if k != "breakouts"}
            data["youtube"]["breakouts"] = len(metrics.get("breakouts", []))
            summary["youtube"] = "ok"
            summary["youtube_units"] = yt.spent
        except SourceError as error:
            data["youtube"] = {"error": str(error)}
            summary["youtube"] = f"falhou: {error}"
    store.finish_run(run_id, summary)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args, cfg, http, store) -> int:
    print(f"relatório: {report.write(store, cfg, args.run)}")
    return 0


def cmd_status(args, cfg, http, store) -> int:
    day = YouTube.quota_day()
    used = store.quota_used(day)
    print(f"cota do YouTube hoje ({day}, horário do Pacífico): {used} de {cfg.youtube.daily_quota} unidades")
    print(f"chave do YouTube: {'definida' if os.environ.get('YOUTUBE_API_KEY') else 'ausente'}")
    for row in store.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 5"):
        print(f"execução {row['id']} · {row['command']} · {row['started_at']} · {row['summary'] or 'em andamento'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radar", description="Radar de nichos: coletor gratuito de sinais de demanda.")
    parser.add_argument("--config", help="caminho do radar.toml (padrão: ./radar.toml)")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="coleta tudo, valida, mede o YouTube e gera o relatório")
    run.add_argument("--skip-youtube", action="store_true", help="não gasta cota do YouTube")
    run.add_argument("--max-candidates", type=int, help="limite de termos validados")
    run.set_defaults(handler=cmd_run)

    check = commands.add_parser("check", help="valida um termo em todas as fontes")
    check.add_argument("term")
    check.add_argument("--market", default="br")
    check.add_argument("--youtube", action="store_true", help="mede também no YouTube (cerca de 102 unidades)")
    check.set_defaults(handler=cmd_check)

    rep = commands.add_parser("report", help="regera o relatório HTML")
    rep.add_argument("--run", type=int, help="número da execução (padrão: a última)")
    rep.set_defaults(handler=cmd_report)

    status = commands.add_parser("status", help="cota usada e últimas execuções")
    status.set_defaults(handler=cmd_status)

    args = parser.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except (FileNotFoundError, ValueError, TypeError) as error:
        print(f"configuração inválida: {error}", file=sys.stderr)
        return 2
    store = Store(cfg.db_path)
    try:
        return args.handler(args, cfg, Http(contact=cfg.contact), store)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        store.close()
