"""REPL companion mode — scheletro Wave 1 (design doc §8) + Task 3 + Task 4
(UI ``rich``, design doc §7) + Wave 2 (design doc §11.6/roadmap Fase 8 Wave 2:
resume di una sessione interrotta, avvio da FEN/PGN parziale, "auto-hint con
soglia" opt-in).

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

``run()`` accetta ``resume_game_id``/``start_fen`` (parsing/validazione di
argv, incluso il PGN parziale, fatti a monte in ``__main__.py`` — qui sono
già valori pronti all'uso, mai testo grezzo da riparsare) e
``auto_hint_threshold`` (modalità silenziosa a soglia, opt-in). Default
(``None``, parametro omesso): comportamento storico INVARIATO al carattere —
pannello completo dopo OGNI mossa, chiunque l'abbia giocata
(``_run_advice_step`` con soglia ``None`` chiama semplicemente
``_show_advice``, identico a prima). Se l'utente passa una soglia: il
consiglio "in avanti" per la mossa che il player sta per scegliere resta
SEMPRE a pannello pieno (è il cuore della companion mode, la soglia non si
applica lì); solo dopo che il player registra la PROPRIA mossa si confronta
l'eval con quello cachato appena prima che la giocasse — oltre soglia,
pannello pieno con un framing esplicito ("hai perso ~Ncp, era meglio X");
entro soglia, un riconoscimento minimo (``ui.render_quiet_ack``), per
ridurre il rumore invece di aggiungerne. ``/hint`` resta disponibile
on-demand in ENTRAMBE le modalità, invariato."""

from __future__ import annotations

from rich.console import Console

from . import ui
from .autohint import exceeds_threshold
from .backend_client import BackendClient, BackendError, BackendUnavailable
from .config import BASE_URL
from .effort import prompt_effort_choice, skill_level_for_effort
from .local_engine import LocalEngineAdvisor, open_local_engine
from .session import CompanionSession, is_players_turn


def _prompt_player_color(input_func=input, output_func=print) -> str:
    while True:
        choice = input_func("Con che colore giochi TU sul sito esterno? [w/b] ").strip().lower()
        if choice in ("w", "white"):
            return "white"
        if choice in ("b", "black"):
            return "black"
        output_func("Rispondi 'w' o 'b'.")


def _show_advice(session: CompanionSession, console: Console) -> dict:
    """Spinner mentre il motore locale calcola + pannello eval/mossa
    migliore/candidate + pezzi in presa a colori + lista mosse stilizzata
    (Task 4, design doc §7). Unico punto della REPL che tocca il rendering
    rich per il loop di consiglio — chiamato dopo ogni mossa registrata (in
    modalità sempre-attiva) e da ``/hint`` (in ENTRAMBE le modalità).

    Ritorna l'advice calcolata (Wave 2, design doc §10): additivo, i
    chiamanti esistenti che ignorano il valore di ritorno restano invariati
    — serve a ``_run_advice_step``/``_seed_pending_advice`` per cachare
    l'eval "pre-mossa" del player senza una seconda chiamata all'engine."""
    with ui.advice_status(console):
        advice = session.advice()
        threats = session.threats()
    ui.render_advice(console, advice)
    ui.render_threats(console, threats)
    ui.render_move_list(console, session.move_history_san())
    return advice


def _seed_pending_advice(session: CompanionSession, console: Console, auto_hint_threshold: int | None) -> None:
    """Wave 2 (design doc §10) — SOLO in modalità silenziosa: se la
    primissima mossa da riportare in sessione è già quella del player (es.
    gioca il bianco sul sito esterno), mostra subito il consiglio "in
    avanti" e ne cacha l'eval — stesso trattamento che il loop principale
    riserva al consiglio mostrato dopo la mossa dell'avversario, solo
    anticipato all'apertura sessione perché qui non c'è ancora stata alcuna
    mossa dell'avversario da cui farlo scattare. Nessun effetto (nessuna
    chiamata) in modalità sempre-attiva — ``auto_hint_threshold is None``
    esce subito."""
    if auto_hint_threshold is None:
        return
    if not is_players_turn(session.board, session.player_color):
        return
    advice = _show_advice(session, console)
    session.remember_pending_player_advice(advice)


def _run_advice_step(
    session: CompanionSession,
    console: Console,
    auto_hint_threshold: int | None,
    was_players_move: bool,
) -> None:
    """Passo di consiglio dopo una mossa registrata con successo — branch
    fra modalità sempre-attiva (default, Wave 1, invariata) e modalità
    silenziosa a soglia (Wave 2, opt-in, design doc §10).

    ``was_players_move`` va calcolato dal CHIAMANTE PRIMA di registrare la
    mossa (``is_players_turn(session.board, session.player_color)`` sulla
    board precedente) — dopo ``register_move`` il turno è già passato
    all'altro lato, quindi va catturato prima o si perde l'informazione.

    - ``auto_hint_threshold is None`` → comportamento storico invariato al
      carattere: pannello completo dopo OGNI mossa, chiunque l'abbia
      giocata (``_show_advice`` diretto, nessuna logica di soglia toccata).
    - Altrimenti, dopo la mossa dell'AVVERSARIO (``was_players_move`` False
      — tocca ora al player scegliere la propria) → pannello completo
      comunque: è il consiglio "in avanti" che serve per decidere la
      propria mossa, il cuore della companion mode — la soglia si applica
      solo a valle, mai qui. L'eval di questo consiglio viene cachata in
      sessione come "pre-mossa" per calcolare il delta quando arriverà la
      mossa del player.
    - Dopo la mossa del PLAYER (``was_players_move`` True) → calcola il
      delta rispetto all'eval cachata (``session.consume_pending_player_loss``,
      ``None`` se non ce n'era una, es. prima mossa di una sessione dove
      gioca il bianco — mai un pannello forzato su un dato che non
      abbiamo): entro soglia → riconoscimento minimo; oltre soglia →
      framing esplicito + LO STESSO pannello completo della modalità
      sempre-attiva (mai un'informazione nascosta quando l'errore è
      grave)."""
    if auto_hint_threshold is None:
        _show_advice(session, console)
        return

    if not was_players_move:
        advice = _show_advice(session, console)
        session.remember_pending_player_advice(advice)
        return

    with ui.advice_status(console):
        advice = session.advice()
        threats = session.threats()
    delta = session.consume_pending_player_loss(advice["eval_cp"])
    loss_cp = delta["loss_cp"]

    if exceeds_threshold(loss_cp, auto_hint_threshold):
        ui.render_threshold_alert(console, loss_cp, delta["best_move_san"])
        ui.render_advice(console, advice)
        ui.render_threats(console, threats)
        ui.render_move_list(console, session.move_history_san())
    else:
        ui.render_quiet_ack(console, loss_cp)


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
    auto_hint_threshold: int | None = None,
) -> None:
    """``resume_game_id``/``start_fen`` (Wave 2, design doc §11.6): modi
    alternativi di avviare/riprendere una sessione, mutuamente esclusivi tra
    loro (validato a monte in ``__main__.py``, mai qui).

    ``auto_hint_threshold`` (Wave 2, design doc §10): ``None`` (default,
    parametro omesso) = comportamento storico Wave 1 invariato, pannello
    completo dopo ogni mossa. Un intero attiva la modalità silenziosa —
    pannello completo automatico solo quando la mossa DEL PLAYER perde più
    di ``auto_hint_threshold`` cp rispetto al meglio disponibile, altrimenti
    un riconoscimento minimo. Vedi ``_run_advice_step``/``_seed_pending_advice``
    per la logica di branching, isolata da questo loop. Componibile con
    ``resume_game_id``/``start_fen``: si applica a prescindere da come la
    sessione è stata avviata."""
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

    if auto_hint_threshold is not None:
        output_func(
            f"Modalità silenziosa attiva: pannello completo automatico solo se perdi più di "
            f"{auto_hint_threshold} cp rispetto al meglio disponibile; altrimenti un riconoscimento "
            f"minimo. /hint resta disponibile in ogni momento."
        )
        _seed_pending_advice(session, console, auto_hint_threshold)

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

            # Va catturato PRIMA di register_move: dopo, il turno è già
            # passato all'altro lato (Wave 2, design doc §10).
            was_players_move = is_players_turn(session.board, session.player_color)

            outcome = session.register_move(line)
            if not outcome["ok"]:
                output_func(f"  {outcome['error']}")
                continue

            _run_advice_step(session, console, auto_hint_threshold, was_players_move)

            if session.is_game_over():
                _announce_game_over(session, input_func, output_func)
    finally:
        session.close()
        output_func("Sessione chiusa.")


def main(auto_hint_threshold: int | None = None) -> None:
    run(auto_hint_threshold=auto_hint_threshold)
