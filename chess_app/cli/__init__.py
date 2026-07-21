"""Chess Lab — modalità CLI/companion (docs/cli-companion-mode-design.md).

Un compagno da terminale che segue una partita giocata ALTROVE (lichess.org,
chess.com, scacchiera fisica): l'utente riporta a mano le mosse di entrambi i
lati e riceve consigli (mossa migliore + eval + pezzi in presa) da un motore
Stockfish locale a bassa latenza, mentre il backend fa da sistema di record
(persistenza, PGN, analisi — riusati as-is, nessuna modifica).

Architettura hybrid (design doc §4): NON un thin client HTTP e NON uno script
standalone che duplica il backend. Due responsabilità nettamente separate:
  - `local_engine.py` — Stockfish locale, aperto una volta a sessione, per il
    loop di consiglio dal vivo (latenza-sensibile).
  - `backend_client.py` — mirroring della partita verso il backend esistente
    via HTTP (persistenza/PGN/analisi/pezzi-in-presa — non latenza-sensibile,
    logica server sostanziale da riusare, non duplicare).

Wave 1 (questo package): REPL scheletro, effort→Skill, motore locale,
mirroring HTTP, loop di consiglio. Fuori scope qui: comandi `/pgn`/`/analyze`
e rifinitura UI con `rich` (follow-up separati, vedi design doc §8/§9).

Eseguibile come `python -m chess_app.cli` (vedi `__main__.py`).
"""
