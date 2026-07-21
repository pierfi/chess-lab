"""REPL companion mode — scheletro Wave 1 (design doc §8).

Plain ``print``/``input``: niente `rich` in questo task (rifinitura UI è un
follow-up separato), niente comandi ``/pgn``/``/analyze`` (altro follow-up
separato). Qui: selezione effort, apertura sessione companion (o degrado
"solo consigli" se il backend non risponde), loop mossa-per-mossa con
consiglio dopo ogni mossa registrata, comandi ``/undo``/``/hint``/``/quit``.
"""

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
            line = input_func(f"{prompt} (mossa SAN/UCI, o /undo /hint /quit): ").strip()
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
            if line.startswith("/"):
                output_func("  Comando sconosciuto.")
                continue

            outcome = session.register_move(line)
            if not outcome["ok"]:
                output_func(f"  {outcome['error']}")
                continue

            _print_advice(session.advice(), output_func)
            _print_threats(session.threats(), output_func)
    finally:
        session.close()
        output_func("Sessione chiusa.")


def main() -> None:
    run()
