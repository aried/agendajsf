"""Static HTML report of one run. No scripts, no external assets."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .textutil import recurring_terms

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b6862;--line:#dedad2;--card:#fff;--accent:#0b6b52;
--good:#0b6b52;--warn:#9a5b00;--bad:#a3271f}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--fg:#eceae4;--muted:#a09c94;
--line:#34322e;--card:#1f1e1b;--accent:#5fd0ac;--good:#5fd0ac;--warn:#e6b05c;--bad:#f08b80}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 6px}
h3{font-size:15px;margin:20px 0 6px}
p{margin:6px 0 12px;max-width:72ch}.muted{color:var(--muted)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;white-space:nowrap;color:var(--muted)}
tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:var(--accent)}
.ok{color:var(--good)}.fail{color:var(--bad)}.warn{color:var(--warn)}
.tag{display:inline-block;padding:1px 8px;border:1px solid var(--line);border-radius:999px;
font-size:12px;white-space:nowrap}
ul{margin:6px 0 12px;padding-left:20px}
"""

SOURCE_NAMES = {
    "google_trends": "Google Trends (em alta agora)",
    "wikipedia": "Wikipedia (artigos que mais cresceram)",
    "hackernews": "Hacker News (página inicial)",
    "github": "GitHub (repositórios novos mais estrelados)",
    "mastodon": "Mastodon (hashtags em alta)",
    "bluesky": "Bluesky (assuntos em alta)",
    "manual": "Anotações manuais",
    "youtube": "YouTube Data API",
}


def _num(value) -> str:
    if value is None:
        return "–"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}".replace(".", ",")
    return f"{int(value):,}".replace(",", ".")


def _pct(value) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def _table(headers: list[tuple[str, bool]], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="muted">Nada a mostrar nesta execução.</p>'
    head = "".join(f'<th class="{"num" if numeric else ""}">{escape(name)}</th>' for name, numeric in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="{"num" if headers[i][1] else ""}">{cell}</td>' for i, cell in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _link(url: str | None, text: str) -> str:
    return f'<a href="{escape(url)}">{escape(text)}</a>' if url else escape(text)


def _sources_section(db, run_id) -> str:
    rows = []
    for row in db.execute(
        "SELECT source, market, ok, items, error FROM source_log WHERE run_id = ? ORDER BY rowid", (run_id,)
    ):
        status = '<span class="ok">respondeu</span>' if row["ok"] else '<span class="fail">falhou</span>'
        rows.append([
            escape(SOURCE_NAMES.get(row["source"], row["source"])),
            escape((row["market"] or "global").upper()),
            status,
            _num(row["items"]),
            escape(row["error"] or ""),
        ])
    return "<h2>Saúde das fontes</h2>" + _table(
        [("Fonte", False), ("Mercado", False), ("Situação", False), ("Itens", True), ("Erro", False)], rows
    )


def _youtube_section(db, run_id, cfg) -> str:
    measures = db.execute(
        "SELECT * FROM yt_measures WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    if not measures:
        return (
            "<h2>Oferta e demanda no YouTube</h2>"
            '<p class="warn">Esta execução não mediu o YouTube. Sem a chave da YouTube Data API '
            "o radar mostra só os sinais de fora da plataforma, que não dizem quem está "
            "recebendo as visualizações.</p>"
        )
    kinds = {"query": "termo", "scan": "varredura sem termo", "popular": "mais populares"}
    rows, breakouts = [], []
    for measure in measures:
        metrics = json.loads(measure["metrics"])
        if not metrics.get("n_videos"):
            continue
        rows.append((
            metrics.get("index") or 0,
            [
                escape(measure["query"] or f"({kinds[measure['kind']]})"),
                escape(measure["market"].upper()),
                _num(metrics.get("index")),
                _num(metrics["views_total"]),
                _num(metrics["n_videos"]),
                _pct(metrics["small_views_share"]),
                _pct(metrics["new_views_share"]),
                _num(len(metrics["breakouts"])),
                _pct(metrics["top3_views_share"]),
                _pct(metrics["short_share"]),
                "–" if metrics.get("momentum") is None else _num(float(metrics["momentum"])),
            ],
        ))
        if metrics["breakouts"]:
            marks = ",".join("?" * len(metrics["breakouts"]))
            for video in db.execute(
                f"SELECT v.*, c.title AS channel, c.subs, c.created_at FROM yt_videos v"
                f" LEFT JOIN yt_channels c ON c.channel_id = v.channel_id AND c.collected_at = v.collected_at"
                f" WHERE v.measure_id = ? AND v.video_id IN ({marks}) ORDER BY v.views DESC",
                [measure["id"], *metrics["breakouts"]],
            ):
                breakouts.append((measure, video))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    y = cfg.youtube
    html = (
        "<h2>Oferta e demanda no YouTube</h2>"
        f"<p>Cada linha olha os vídeos mais vistos publicados nos últimos {y.window_days} dias. "
        f"Canal pequeno tem menos de {_num(y.small_subs)} inscritos; canal novo tem menos de "
        f"{y.new_days} dias. O índice é uma heurística de 0 a 100, não uma previsão.</p>"
        + _table(
            [("Termo", False), ("Mercado", False), ("Índice", True), ("Views", True), ("Vídeos", True),
             ("Views em canais pequenos", True), ("Views em canais novos", True), ("Estouros", True),
             ("Views nos 3 maiores", True), ("Até 3 min", True), ("Impulso externo", True)],
            [row for _, row in rows],
        )
    )
    seen, video_rows, titles = set(), [], []
    for measure, video in breakouts:
        if video["video_id"] in seen:
            continue
        seen.add(video["video_id"])
        titles.append(video["title"] or "")
        video_rows.append([
            _link(f"https://www.youtube.com/watch?v={video['video_id']}", video["title"] or video["video_id"]),
            _link(f"https://www.youtube.com/channel/{video['channel_id']}", video["channel"] or "?"),
            _num(video["subs"]),
            _num(video["views"]),
            escape((video["created_at"] or "")[:10]),
            escape((video["published_at"] or "")[:10]),
            escape(measure["market"].upper()),
            escape(measure["query"] or kinds[measure["kind"]]),
        ])
    html += (
        "<h3>Vídeos de canais pequenos muito acima do próprio tamanho</h3>"
        f"<p>Ao menos {_num(y.breakout_views)} views e {y.breakout_ratio:g} vezes o número de "
        "inscritos. É o sinal mais direto de que o YouTube está distribuindo um assunto para "
        "quem ainda não tem audiência.</p>"
        + _table(
            [("Vídeo", False), ("Canal", False), ("Inscritos", True), ("Views", True),
             ("Canal criado", False), ("Publicado", False), ("Mercado", False), ("Origem", False)],
            video_rows,
        )
    )
    terms = recurring_terms(titles)
    if terms:
        html += "<p>Termos que se repetem nesses títulos: " + ", ".join(
            f"{escape(term)} ({count})" for term, count in terms
        ) + ".</p>"
    return html


def _trend_cell(result: dict | None) -> str:
    if not result:
        return "–"
    if result.get("error"):
        return '<span class="fail">sem resposta</span>'
    parts = []
    if result.get("label"):
        parts.append(f'<span class="tag">{escape(result["label"])}</span>')
    if result.get("ratio_short") is not None:
        parts.append(f'7d/28d {_num(float(result["ratio_short"]))}')
    if result.get("ratio_long") is not None:
        parts.append(f'28d/28d {_num(float(result["ratio_long"]))}')
    return " ".join(parts) or "–"


def _checks_section(db, run_id) -> str:
    rows = []
    for check in db.execute("SELECT * FROM checks WHERE run_id = ? ORDER BY id", (run_id,)):
        data = json.loads(check["data"])
        wiki = data.get("wikipedia") or {}
        news = data.get("news") or {}
        suggest = (data.get("suggest") or {}).get("suggestions") or []
        if news.get("error"):
            news_cell = '<span class="fail">sem resposta</span>'
        elif news:
            cap = "+" if news.get("capped") else ""
            news_cell = f'{news.get("items_7d", 0)} / {news.get("items_30d", 0)}{cap}'
        else:
            news_cell = "–"
        wiki_cell = _trend_cell(wiki)
        if wiki.get("matched") is False:
            wiki_cell = '<span class="muted">sem artigo correspondente</span>'
        elif wiki.get("article"):
            wiki_cell = f'{_link(wiki.get("url"), wiki["article"])}<br>{_num(wiki.get("views_7d"))} views em 7d<br>{wiki_cell}'
        rows.append((
            data.get("momentum") or -1,
            [
                escape(check["term"]),
                escape(check["market"].upper()),
                escape(check["origin"] or ""),
                "–" if data.get("momentum") is None else _num(float(data["momentum"])),
                wiki_cell,
                news_cell,
                escape("; ".join(suggest[:5])) or "–",
                _num(data.get("trending_days")),
            ],
        ))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    return (
        "<h2>Validação entre fontes</h2>"
        "<p>Impulso externo vai de 0 a 1 e resume o crescimento fora do YouTube. "
        "“Pico recente” costuma ser notícia do dia; para um canal, “alta sustentada” vale mais. "
        "As razões comparam os últimos 7 dias com os 28 anteriores, e esses 28 com os 28 de antes.</p>"
        + _table(
            [("Termo", False), ("Mercado", False), ("Origem", False), ("Impulso", True),
             ("Wikipedia", False),
             ("Google News 7d / 30d", True), ("YouTube sugere", False), ("Dias em alta", True)],
            [row for _, row in rows],
        )
    )


def _signals_section(db, run_id) -> str:
    html = "<h2>Sinais brutos por fonte</h2>"
    groups = db.execute(
        "SELECT DISTINCT source, market FROM signals WHERE run_id = ? ORDER BY source, market", (run_id,)
    ).fetchall()
    for group in groups:
        signals = db.execute(
            "SELECT * FROM signals WHERE run_id = ? AND source = ? AND market IS ? ORDER BY"
            " CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank LIMIT 15",
            (run_id, group["source"], group["market"]),
        ).fetchall()
        name = SOURCE_NAMES.get(group["source"].split(":")[0], group["source"])
        where = f' · {group["market"].upper()}' if group["market"] else ""
        rows = []
        for signal in signals:
            extra = json.loads(signal["extra"] or "{}")
            detail = extra.get("description") or "; ".join(extra.get("news") or [])
            rows.append([
                _link(signal["url"], signal["term"]),
                escape(signal["label"] or ""),
                escape(detail or ""),
            ])
        html += f"<h3>{escape(name)}{escape(where)}</h3>" + _table(
            [("Termo", False), ("Medida", False), ("Contexto", False)], rows
        )
    return html


METHOD = """
<h2>Método e limites</h2>
<ul>
<li>O YouTube não publica volume de busca. O radar mede demanda pelo que já aconteceu:
visualizações de vídeos recentes e quem as recebeu.</li>
<li>A busca da API devolve no máximo 50 vídeos por página e não é um censo; trate os números
como amostra dos mais vistos, não como o total do assunto.</li>
<li>Google Trends "em alta agora" mostra picos do dia, quase sempre notícia, esporte e
celebridade. Serve para validar, raramente para escolher nicho.</li>
<li>Hacker News e GitHub só enxergam tecnologia. Mastodon e Bluesky têm público pequeno e
enviesado; a ausência de um tema ali não prova nada.</li>
<li>TikTok, Instagram, X e Reddit não têm API gratuita utilizável por um script. O que você
observar lá entra pelo arquivo <code>data/manual_signals.csv</code>.</li>
<li>Os pesos do índice e os limiares (canal pequeno, estouro, formas de tendência) são
escolhas heurísticas, não validadas contra resultados reais de canais.</li>
</ul>
"""


def build(store, cfg, run_id: int | None = None) -> str:
    db = store.db
    run = (
        db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run_id
        else store.latest_run()
    )
    if run is None:
        raise ValueError("nenhuma execução registrada; rode `python -m radar run` primeiro")
    summary = json.loads(run["summary"] or "{}")
    intro = (
        f'<p class="muted">Execução {run["id"]} · iniciada em {escape(run["started_at"])} UTC · '
        f'{_num(summary.get("signals"))} sinais · {_num(summary.get("candidates"))} candidatos · '
        f'YouTube: {escape(str(summary.get("youtube", "–")))}'
        + (f' ({_num(summary.get("youtube_units"))} unidades de cota)' if summary.get("youtube_units") else "")
        + "</p>"
    )
    body = (
        "<h1>Radar de nichos</h1>" + intro
        + _youtube_section(db, run["id"], cfg)
        + _checks_section(db, run["id"])
        + _sources_section(db, run["id"])
        + _signals_section(db, run["id"])
        + METHOD
    )
    return (
        '<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Radar de nichos</title><style>{CSS}</style></head><body><main>{body}</main></body></html>"
    )


def write(store, cfg, run_id: int | None = None, path: Path | None = None) -> Path:
    target = path or cfg.data_dir / "report.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(store, cfg, run_id), encoding="utf-8")
    return target
