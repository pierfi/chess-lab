"""Bootstrap da PGN parziale (Wave 2, design doc §10 backlog "metodi di
input alternativi" → promosso a Wave 2 dalla roadmap Fase 8): l'utente
incolla una partita letta altrove (o solo un frammento) e la sessione
companion riparte dalla posizione RISULTANTE invece che dalla posizione
standard.

Nessun endpoint nuovo, nessuna chiamata di rete: pure parsing client-side con
``python-chess``, stesso pattern tollerante di ``chess.pgn.read_game()`` già
usato da ``POST /games/import`` lato backend (vedi ``backend/main.py``) — ma
qui ci si ferma al FEN risultante, che alimenta il parametro ``start_fen``
GIÀ esistente di ``POST /game/companion/new`` (nessun nuovo meccanismo lato
server, nessuna duplicazione della logica di parsing/validazione di
``/games/import``, solo lo stesso pattern applicato in locale).

Nota di design: le mosse del PGN incollato non diventano righe ``moves``
della sessione companion sul backend — solo la POSIZIONE conta, esattamente
come un drill di finali che parte da uno ``start_fen`` custom. La partita
companion tracciata (e quindi PGN/analisi/storico) comincia da lì in avanti,
non include il prefisso incollato. Questa è una scelta deliberata (vedi
istruzioni Wave 2): non richiede alcun endpoint nuovo, a differenza di
un'alternativa che rigiocasse ogni mossa via
``POST /game/{id}/companion/move`` una alla volta per preservarle come
storico reale."""

from __future__ import annotations

import io

import chess
import chess.pgn


def fen_from_partial_pgn(pgn_text: str) -> str:
    """Ritorna il FEN della posizione dopo aver rigiocato la mainline del PGN
    incollato.

    Solleva ``ValueError`` con un messaggio già pronto da mostrare
    all'utente se il PGN è vuoto/non interpretabile, ha un'intestazione FEN
    di partenza malformata, o non contiene alcuna mossa. Il controllo di
    validità vero è "zero mosse nella mainline" — non ``parsed.errors``:
    ``chess.pgn.read_game()`` è tollerante e produce comunque un ``Game``
    valido (``errors`` vuoto) anche per input spazzatura, stessa
    osservazione documentata per ``POST /games/import``."""
    parsed = chess.pgn.read_game(io.StringIO(pgn_text))
    if parsed is None or parsed.errors:
        raise ValueError("PGN non valido o non interpretabile.")

    try:
        board = chess.Board(parsed.headers["FEN"]) if parsed.headers.get("FEN") else chess.Board()
    except ValueError:
        raise ValueError("FEN di partenza non valida nelle intestazioni del PGN.") from None

    moves = list(parsed.mainline_moves())
    if not moves:
        raise ValueError("Il PGN non contiene alcuna mossa.")

    for move in moves:
        board.push(move)

    return board.fen()
