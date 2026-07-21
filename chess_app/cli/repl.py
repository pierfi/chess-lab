"""REPL companion mode — scheletro Wave 1 (design doc §8) + Task 3.

Plain ``print``/``input``: niente `rich` in questo task (rifinitura UI è un
follow-up separato, Task 4). Qui: selezione effort, apertura sessione
companion (o degrado "solo consigli" se il backend non risponde), loop
mossa-per-mossa con consiglio dopo ogni mossa registrata, comandi
``/undo``/``/hint``/``/pgn``/``/analyze``/``/quit`` + riepilogo automatico di
fine partita con offerta di analisi on-demand (mai forzata, design doc §5:
`/game/analyze` resta una chiamata Stockfish esplicita e separata dal
gameplay, stessa filosofia dell'app principale)."""

from __future__ import annotations

from .backend_client import BackendClient
from .config import BASE_URL
from .effort import prompt_effort_choice, skill_level_for_effort
from .local_engine import open_local_engine
from .session import CompanionSession


def _prompt_player_color(input_func=input, output_func=print) -> str:
    while True:
        choice = input_func("Con che colore giochi TU sul sito esterno? [w/b] ").strip().lower()
        if choice in ("w", "white"):
            return "white"
        if choice in ("b", "black"):
            return "black"
        output_func("Rispondi 'w' o 'b'.")


def _format_eval(eval_cp: int | None) -> str:
    if eval_cp is None:
        return "n/d"
    if abs(eval_cp) >= 10000:
        return "matto in vista per il bianco" if eval_cp > 0 else "matto in vista per il nero"
    return f"{eval_cp / 100:+.2f}"


def _print_advice(advice: dict, output_func=print) -> None:
    output_func(f"  Valutazione: {_format_eval(advice['eval_cp'])}")
    for i, line in enumerate(advice["lines"], start=1):
        output_func(f"  {i}. {line['move_san']} ({line['score_cp']:+d} cp)")


def _print_threats(labeled: dict | None, output_func=print) -> None:
    if labeled is None:
        return
    pieces = labeled["in_presa"]
    if not pieces:
        return
    output_func(f"  Attenzione — {labeled['label']}:")
    for p in pieces:
        attackers = ", ".join(p["attackers"])
        output_func(f"    {p['square']} ({p['piece']}) attaccato da {attackers}")


def _format_pgn_outcome(outcome: dict) -> str:
    if outcome["ok"]:
        return f"  PGN scritto in {outcome['path']}."
    return f"  {outcome['error']}"


def _run_pgn(session: CompanionSession, path: str | None, input_func=input, output_func=print) -> None:
    """Comando ``/pgn`` — accetta il percorso come argomento (``/pgn out.pgn``)
    o lo richiede interattivamente se omesso. Messaggio di indisponibilità
    chiaro in modalità degradata (design doc §4), gestito da
    ``session.write_pgn`` — nessuna logica di degrado duplicata qui."""
    if path is None:
        path = input_func("Percorso file di output per il PGN: ").strip()
    outcome = session.write_pgn(path)
    output_func(_format_pgn_outcome(outcome))


def _format_analysis_summary(result: dict) -> list[str]:
    """Riepilogo leggibile della risposta di ``POST /game/analyze`` — stesso
    shape documentato in CLAUDE.md ("Risposta tipo `/game/analyze`"): mosse
    totali, accuracy, conteggi per classificazione, e il dettaglio dei ply
    blunder/mistake (ply, SAN, loss_cp) così l'utente vede ESATTAMENTE dove
    ha sbagliato, non solo un conteggio aggregato."""
    lines = [
        f"  Mosse totali: {result['total_moves']}",
        f"  Accuracy: {result['accuracy_score']:.1f}%",
        f"  Blunder: {result['blunders']}  Mistake: {result['mistakes']}  Inaccuracy: {result['inaccuracies']}",
    ]
    flagged = [m for m in result["moves"] if m["classification"] in ("blunder", "mistake")]
    if flagged:
        lines.append("  Da rivedere:")
        for m in flagged:
            lines.append(
                f"    Ply {m['ply']} ({m['color']}) {m['move_san']}: "
                f"{m['classification']} (-{m['loss_cp']} cp)"
            )
    return lines


def _run_analyze(session: CompanionSession, output_func=print) -> None:
    """Comando ``/analyze`` — chiama `POST /game/analyze` (riuso puro,
    design doc §5) e stampa il riepilogo. Messaggio di indisponibilità
    chiaro in modalità degradata o partita senza mosse, gestito da
    ``session.analyze`` (stesso 400 del backend, ririportato così com'è)."""
    outcome = session.analyze()
    if not outcome["ok"]:
        output_func(f"  {outcome['error']}")
        return
    for line in _format_analysis_summary(outcome["result"]):
        output_func(line)


def _prompt_yes_no(question: str, input_func=input) -> bool:
    answer = input_func(question).strip().lower()
    return answer in ("s", "si", "sì", "y", "yes")


def _announce_game_over(session: CompanionSession, input_func=input, output_func=print) -> None:
    """Riepilogo automatico di fine partita (Task 3): scatta quando la
    posizione tracciata risulta terminata (`session.is_game_over()`, che
    riusa il segnale del backend o, in degrado, `chess.Board.is_game_over()`
    locale — mai una reimplementazione della logica di game-over). L'analisi
    completa NON parte mai da sola: è una vera ricerca Stockfish, va chiesta
    esplicitamente, stessa filosofia di `/game/analyze` nel resto dell'app."""
    output_func(f"Partita terminata dopo {session.move_count()} mosse.")
    if session.degraded:
        output_func("  (Analisi non disponibile in modalità degradata.)")
        return
    if _prompt_yes_no("Vuoi eseguire l'analisi ora? [s/N] ", input_func):
        _run_analyze(session, output_func)


def run(base_url: str = BASE_URL, input_func=input, output_func=print) -> None:
    output_func("Chess Lab — Companion mode (CLI)")
    output_func("Segui una partita giocata altrove: riporta le mosse, ricevi consigli.")

    player_color = _prompt_player_color(input_func, output_func)
    effort_elo = prompt_effort_choice(input_func, output_func)

    backend = BackendClient(base_url=base_url)
    engine_advisor = open_local_engine(skill_level_for_effort(effort_elo))
    session = CompanionSession(backend, engine_advisor)

    session.start(player_color, effort_elo)
    if session.degraded:
        output_func("Impossibile contattare il backend: modalità solo-consigli.")
        output_func("PGN/storico/analisi non saranno disponibili in questa sessione.")
    else:
        output_func(f"Sessione companion creata (game_id={session.game_id}).")

    try:
        while True:
            prompt = session.turn_prompt()
            line = input_func(
                f"{prompt} (mossa SAN/UCI, o /undo /hint /pgn /analyze /quit): "
            ).strip()
            if not line:
                continue
            if line == "/quit":
                break
            if line == "/undo":
                outcome = session.undo()
                output_func(f"  {outcome['error']}" if not outcome["ok"] else "  Mossa annullata.")
                continue
            if line == "/hint":
                _print_advice(session.advice(), output_func)
                _print_threats(session.threats(), output_func)
                continue
            if line == "/pgn" or line.startswith("/pgn "):
                parts = line.split(maxsplit=1)
                arg = parts[1].strip() if len(parts) > 1 else None
                _run_pgn(session, arg, input_func, output_func)
                continue
            if line == "/analyze":
                _run_analyze(session, output_func)
                continue
            if line.startswith("/"):
                output_func("  Comando sconosciuto.")
                continue

            outcome = session.register_move(line)
            if not outcome["ok"]:
                output_func(f"  {outcome['error']}")
                continue

            _print_advice(session.advice(), output_func)
            _print_threats(session.threats(), output_func)

            if session.is_game_over():
                _announce_game_over(session, input_func, output_func)
    finally:
        session.close()
        output_func("Sessione chiusa.")


def main() -> None:
    run()
