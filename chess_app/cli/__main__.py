"""Entry point per ``python -m chess_app.cli`` (design doc §11.6 — nessun
console-script per questa MVP, un ``__main__.py`` basta).

Wave 2 (roadmap Fase 8 Wave 2, design doc §11.6/§10 "metodi di input
alternativi" promosso da backlog): aggiunge il parsing di riga di comando per
tre modi di avvio/ripresa alternativi al flusso interattivo standard —
mutuamente esclusivi tra loro:

  --resume GAME_ID   riprende una sessione companion interrotta
                      (GET /game/{id}, vedi cli/session.py CompanionSession.resume)
  --fen FEN          avvia una sessione NUOVA da una posizione FEN custom
                      (start_fen esistente di POST /game/companion/new)
  --pgn TESTO        avvia da una posizione derivata da un PGN parziale
                      incollato inline (testo grezzo come argomento)
  --pgn-file PATH    come --pgn, ma il testo viene letto da file

Nessun flag → comportamento INVARIATO rispetto a Wave 1 (prompt interattivo
colore/effort, posizione standard).

Il parsing/validazione del PGN (python-chess, ``cli/pgn_bootstrap.py`` — stesso
pattern tollerante di ``POST /games/import`` lato backend, mai reimplementato
qui) avviene interamente QUI, prima di chiamare ``repl.run()``: un PGN
malformato o un file illeggibile vanno segnalati e l'uscita deve avvenire
PRIMA di aprire qualunque connessione al backend o motore Stockfish locale,
non a metà sessione."""

import argparse
import sys

from .pgn_bootstrap import fen_from_partial_pgn
from .repl import run


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m chess_app.cli",
        description=(
            "Chess Lab — Companion mode: segui una partita giocata altrove "
            "(Lichess, chess.com, scacchiera fisica) e ricevi consigli dal vivo."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--resume",
        metavar="GAME_ID",
        help="Riprende una sessione companion interrotta con questo game_id.",
    )
    group.add_argument(
        "--fen",
        metavar="FEN",
        help="Avvia una sessione nuova da una posizione FEN custom (invece della standard).",
    )
    group.add_argument(
        "--pgn",
        metavar="TESTO",
        help="Avvia da una posizione derivata dal rigiocare questo PGN parziale (testo inline).",
    )
    group.add_argument(
        "--pgn-file",
        metavar="PATH",
        help="Come --pgn, ma il testo del PGN parziale viene letto da questo file.",
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    resume_game_id = args.resume
    start_fen = args.fen

    pgn_text = None
    if args.pgn is not None:
        pgn_text = args.pgn
    elif args.pgn_file is not None:
        try:
            with open(args.pgn_file, encoding="utf-8") as f:
                pgn_text = f.read()
        except OSError as exc:
            print(f"Impossibile leggere il file PGN '{args.pgn_file}': {exc}")
            sys.exit(1)

    if pgn_text is not None:
        # --pgn/--pgn-file sono un modo per ARRIVARE a uno start_fen, non un
        # canale separato lungo tutto lo stack: da qui in poi è indistinguibile
        # da --fen (stesso parametro di repl.run()).
        try:
            start_fen = fen_from_partial_pgn(pgn_text)
        except ValueError as exc:
            print(f"PGN non valido: {exc}")
            sys.exit(1)

    run(resume_game_id=resume_game_id, start_fen=start_fen)


if __name__ == "__main__":
    main()
