"""Rendering ``rich`` per la companion CLI (Wave 1, Task 4 — "UI rich",
design doc §7/§9).

Isola TUTTO l'uso di ``rich`` in questo unico modulo: il resto della CLI
(``session.py``, ``backend_client.py``, ``local_engine.py``, ``effort.py``,
``config.py``) resta libero da questa dipendenza, e i pezzi di ``repl.py``
già coperti da test con il pattern ``output_func``/``input_func``
(``_format_analysis_summary``, ``_announce_game_over``) restano puro testo —
nessuna modifica al loro comportamento, nessun rischio per quei test.

Ogni funzione qui prende un ``rich.console.Console`` esplicito (mai un
singleton globale): in produzione è un ``Console()`` vero che scrive su
stdout reale; nei test è un ``Console(file=io.StringIO(), force_terminal=
False, width=...)`` — stessa tecnica "capture" suggerita per verificare il
testo renderizzato senza dipendere da un vero terminale né da sequenze ANSI
fragili (``force_terminal=False`` disabilita colori/markup nell'output
catturato, lasciando solo il testo informativo da asserire)."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def format_eval_text(eval_cp: int | None) -> str:
    """Stessa formattazione dell'ex ``_format_eval`` di ``repl.py`` (spostata
    qui perché ora è consumata solo dal rendering rich)."""
    if eval_cp is None:
        return "n/d"
    if abs(eval_cp) >= 10000:
        return "matto in vista per il bianco" if eval_cp > 0 else "matto in vista per il nero"
    return f"{eval_cp / 100:+.2f}"


def move_row_style(rank: int) -> str:
    """Stile della riga di una mossa candidata nel pannello di consiglio: la
    prima (``rank == 1``, la migliore per chi è al tratto — l'ordinamento è
    già garantito da ``LocalEngineAdvisor.advice``) risalta in verde
    grassetto, le altre restano neutre. Funzione pura, separata dal
    rendering ``rich`` per essere verificabile senza dover fare parsing di
    ANSI/markup nei test."""
    return "bold green" if rank == 1 else "white"


def threat_style(label: str) -> tuple[str, str]:
    """(colore, icona) per il pannello "in presa", in base all'etichetta già
    calcolata da ``label_threats()`` — design doc §3.1/§10: i TUOI pezzi in
    presa sono un avviso (rosso, "salvalo prima di muovere"), quelli
    DELL'AVVERSARIO un'opportunità (verde, "puoi prenderlo"). Funzione pura
    per lo stesso motivo di ``move_row_style`` — testabile senza toccare
    ``rich``."""
    is_yours = "tuoi" in label
    return ("red", "⚠") if is_yours else ("green", "★")


def advice_status(console: Console):
    """Spinner mentre il motore locale calcola il consiglio (design doc §7,
    "spinner durante la ricerca dell'engine") — usato come context manager:
    ``with advice_status(console): advice = session.advice()``. Il motore
    locale è già long-lived e a bassa latenza (§4), ma la ricerca a depth
    fissa resta comunque percettibile in una REPL dove si digita in fretta:
    lo spinner comunica che la CLI non si è bloccata."""
    return console.status("[bold cyan]Il motore locale sta pensando...[/bold cyan]", spinner="dots")


def render_advice(console: Console, advice: dict) -> None:
    """Pannello eval + tabella delle righe candidate (MultiPV) — sostituisce
    il vecchio plain-print riga-per-riga. La prima riga (la migliore per chi
    è al tratto, già ordinata da ``LocalEngineAdvisor.advice``) è evidenziata
    in verde grassetto, coerente con l'evidenza ``.last-move``/mossa
    suggerita del resto dell'app."""
    eval_text = format_eval_text(advice["eval_cp"])
    lines = advice["lines"]

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1, 0, 0))
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Mossa", no_wrap=True)
    table.add_column("Eval", justify="right", no_wrap=True)

    if not lines:
        table.add_row("—", "nessuna mossa disponibile", "")
    for i, line in enumerate(lines, start=1):
        move_style = move_row_style(i)
        table.add_row(str(i), f"[{move_style}]{line['move_san']}[/{move_style}]", f"{line['score_cp']:+d} cp")

    console.print(
        Panel(
            table,
            title="[bold]Consiglio motore locale[/bold]",
            subtitle=f"Valutazione: {eval_text}",
            border_style="cyan",
        )
    )


def render_threats(console: Console, labeled: dict | None) -> None:
    """Pannello colorato "in presa" — design doc §3.1/§10: i TUOI pezzi in
    presa sono un avviso (rosso, "salvalo prima di muovere"), quelli
    DELL'AVVERSARIO sono un'opportunità (verde, "puoi prenderlo") — stessa
    etichetta ``label`` già calcolata da ``session.threats()``/
    ``label_threats()``, qui solo tradotta in colore. Nessun pannello se non
    c'è nulla da segnalare (``None`` — modalità degradata/nessun game_id — o
    lista vuota)."""
    if labeled is None or not labeled["in_presa"]:
        return

    color, icon = threat_style(labeled["label"])

    lines = []
    for p in labeled["in_presa"]:
        attackers = ", ".join(p["attackers"])
        lines.append(f"{icon} {p['square']} ({p['piece']}) attaccato da {attackers}")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"[bold {color}]{labeled['label'].capitalize()}[/bold {color}]",
            border_style=color,
        )
    )


def render_quiet_ack(console: Console, loss_cp: int | None) -> None:
    """Riconoscimento MINIMO in modalità silenziosa (Wave 2, auto-hint a
    soglia, design doc §10) per una mossa del player rimasta ENTRO soglia —
    una singola riga attenuata, niente pannelli: l'intero senso della
    modalità è ridurre il rumore, non spostarlo in un pannello più piccolo.

    ``loss_cp`` può essere negativo (la mossa ha superato l'eval "migliore"
    cachata — tipico rumore di ricerca a depth fissa fra due chiamate
    separate all'engine, non un errore da segnalare) o ``None`` (nessuna
    advice "pre-mossa" era stata cachata, es. la primissima mossa di una
    sessione dove il player apre col bianco — "non quantificabile", mai
    trattato come zero)."""
    if loss_cp is None:
        console.print("[dim]  Mossa registrata (delta eval non disponibile).[/dim]")
        return
    console.print(f"[dim]  Mossa entro soglia (Δ{loss_cp:+d} cp rispetto al meglio).[/dim]")


def render_threshold_alert(console: Console, loss_cp: int, best_move_san: str | None) -> None:
    """Framing esplicito mostrato SOPRA il pannello di consiglio completo
    quando la mossa del player SUPERA la soglia configurata (Wave 2, design
    doc §10) — l'unico caso in cui la modalità silenziosa si comporta come
    quella sempre-attiva (Wave 1), con in più il "quanto" e, se noto, il
    "cosa sarebbe stato meglio" — la mossa già suggerita dal motore locale
    PRIMA che il player giocasse, nessuna nuova ricerca dedicata a questo
    scopo."""
    if best_move_san:
        console.print(
            f"[bold yellow]  Hai perso ~{loss_cp} cp rispetto al meglio "
            f"— il motore consigliava {best_move_san}.[/bold yellow]"
        )
    else:
        console.print(f"[bold yellow]  Hai perso ~{loss_cp} cp rispetto al meglio.[/bold yellow]")


def render_move_list(console: Console, move_history_san: list[str]) -> None:
    """Lista mosse stilizzata (design doc §7) — coppie bianco/nero numerate,
    stessa convenzione di notazione di una scoresheet/PGN. Nulla se non è
    ancora stata registrata alcuna mossa."""
    if not move_history_san:
        return
    pairs = []
    for i in range(0, len(move_history_san), 2):
        move_number = i // 2 + 1
        white_move = move_history_san[i]
        black_move = move_history_san[i + 1] if i + 1 < len(move_history_san) else ""
        pair = f"[dim]{move_number}.[/dim] {white_move}"
        if black_move:
            pair += f" {black_move}"
        pairs.append(pair)
    console.print(Panel("   ".join(pairs), title="[bold]Mosse[/bold]", border_style="grey50"))
