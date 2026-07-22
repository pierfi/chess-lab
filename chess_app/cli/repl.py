"""REPL companion mode — scheletro Wave 1 (design doc §8) + Task 3 + Task 4
(UI ``rich``, design doc §7) + Wave 2 (design doc §11.6/roadmap Fase 8
Wave 2: resume di una sessione interrotta, avvio da FEN/PGN parziale).

Selezione effort, apertura sessione companion (o degrado "solo consigli" se
il backend non risponde), loop mossa-per-mossa con consiglio dopo ogni mossa
registrata, comandi ``/undo``/``/hint``/``/pgn``/``/analyze``/``/quit`` +
riepilogo automatico di fine partita con offerta di analisi on-demand (mai
forzata, design doc §5: `/game/analyze` resta una chiamata Stockfish
esplicita e separata dal gameplay, stessa filosofia dell'app principale).

Il rendering del loop di consiglio (spinner, pannello eval/mossa migliore,
lista mosse, pezzi in presa a colori) è isolato in ``ui.py`` (unico modulo
che importa ``rich``): qui si costruisce un ``rich.console.Console`` e ci si
appoggia alle sue funzioni. Le due funzioni già coperte da test con il
pattern ``output_func``/``input_func`` (``_format_analysis_summary``,
``_announce_game_over``) restano puro testo, INVARIATE — nessun rischio per
quei test, coerente con la scelta di non introdurre `rich` dove non serve
(il riepilogo di fine partita è testo semplice, non un pannello dal vivo).

``run()`` accetta ora ``resume_game_id``/``start_fen`` (parsing/validazione
di argv, incluso il PGN parziale, fatti a monte in ``__main__.py`` — qui
sono già valori pronti all'uso, mai testo grezzo da riparsare)."""

from __future__ import annotations

from rich.console import Console

from . import ui
from .backend_client import BackendClient, BackendError, BackendUnavailable
from .config import BASE_URL
from .effort import prompt_effort_choice, skill_level_for_effort
from .local_engine import LocalEngineAdvisor, open_local_engine
from .session import CompanionSession


def _prompt_player_color(input_func=input, output_func=print) -> str:
    while True:
        choice = input_func("Con che colore giochi TU sul sito esterno? [w/b] ").strip().lower()
        if choice in ("w", "white"):
            return "white"
        if choice in ("b", "black"):
            return "black"
        output_func("Rispondi 'w' o 'b'.")


def _show_advice(session: CompanionSession, console: Console) -> None:
    """Spinner mentre il motore locale calcola + pannello eval/mossa
    migliore/candidate + pezzi in presa a colori + lista mosse stilizzata
    (Task 4, design doc §7). Unico punto della REPL che tocca il rendering
    rich per il loop di consiglio — chiamato dopo ogni mossa registrata e da
    ``/hint``."""
    with ui.advice_status(console):
        advice = session.advice()
        threats = session.threats()
    ui.render_advice(console, advice)
    ui.render_threats(console, threats)
    ui.render_move_list(console, session.move_history_san())


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


def _resume_session(
    game_id: str,
    base_url: str = BASE_URL,
    output_func=print,
    backend: BackendClient | None = None,
    engine_advisor: LocalEngineAdvisor | None = None,
) -> CompanionSession | None:
    """Riprende una sessione companion interrotta (Wave 2). Ritorna la
    ``CompanionSession`` pronta all'uso, oppure ``None`` se il resume non è
    possibile — in quel caso ha già stampato un messaggio d'errore chiaro e
    ripulito ogni risorsa aperta (nessun crash, nessuna risorsa leaked).

    ``backend``/``engine_advisor`` sono iniettabili (default ``None`` →
    costruiti qui) per gli stessi motivi per cui lo sono altrove nel
    pacchetto (``BackendClient(client=...)``, ``LocalEngineAdvisor(engine=...)``):
    permettono di testare questa funzione con un backend in-process
    (ASGITransport) e un motore stub, senza spawnare un vero Stockfish o
    aprire un vero socket.

    Effort per il motore locale: NON ri-chiesto all'utente. ``engine_elo``
    del record ripreso È l'effort scelto l'ultima volta (persistito 1:1 in
    ``games.engine_elo`` da ``POST /game/companion/new``, vedi
    ``backend/main.py``) — non solo un "default ragionevole": ririchiederlo
    sarebbe pura frizione senza alcun guadagno, quindi si riusa direttamente.

    Nota implementativa: viene fatta UNA chiamata GET di "peek" (per
    conoscere ``engine_elo`` PRIMA di aprire il motore locale con lo Skill
    Level giusto) più quella interna a ``CompanionSession.resume()`` — due
    GET invece di uno. Entrambe read-only, senza alcuna ricerca Stockfish
    lato server (costo trascurabile), a fronte di un ``CompanionSession.resume()``
    che resta autosufficiente e testabile in isolamento (design doc §11.6)."""
    if backend is None:
        backend = BackendClient(base_url=base_url)
    try:
        peek_state = backend.get_game(game_id)
    except (BackendError, BackendUnavailable) as exc:
        output_func(f"Impossibile riprendere la partita '{game_id}': {exc}")
        backend.close()
        return None

    if engine_advisor is None:
        engine_advisor = open_local_engine(skill_level_for_effort(peek_state["engine_elo"]))
    session = CompanionSession(backend, engine_advisor)
    try:
        session.resume(game_id)
    except (BackendError, BackendUnavailable) as exc:
        output_func(f"Impossibile riprendere la partita '{game_id}': {exc}")
        session.close()
        return None

    output_func(
        f"Sessione companion ripresa (game_id={session.game_id}, "
        f"{session.move_count()} mosse già registrate)."
    )
    return session


def run(
    base_url: str = BASE_URL,
    input_func=input,
    output_func=print,
    resume_game_id: str | None = None,
    start_fen: str | None = None,
) -> None:
    console = Console()

    output_func("Chess Lab — Companion mode (CLI)")
    output_func("Segui una partita giocata altrove: riporta le mosse, ricevi consigli.")

    if resume_game_id is not None:
        # Wave 2: resume salta i prompt colore/effort (già noti dalla partita
        # ripresa, vedi _resume_session) — nessun start_fen qui, sarebbe
        # incoerente con "riprendi la partita X" (start_fen è solo per una
        # sessione NUOVA, mutuamente esclusivo con --resume in __main__.py).
        session = _resume_session(resume_game_id, base_url, output_func)
        if session is None:
            return
    else:
        player_color = _prompt_player_color(input_func, output_func)
        effort_elo = prompt_effort_choice(input_func, output_func)

        backend = BackendClient(base_url=base_url)
        engine_advisor = open_local_engine(skill_level_for_effort(effort_elo))
        session = CompanionSession(backend, engine_advisor)

        session.start(player_color, effort_elo, start_fen=start_fen)
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
                _show_advice(session, console)
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

            _show_advice(session, console)

            if session.is_game_over():
                _announce_game_over(session, input_func, output_func)
    finally:
        session.close()
        output_func("Sessione chiusa.")


def main() -> None:
    run()
