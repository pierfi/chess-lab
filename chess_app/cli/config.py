"""Costanti di configurazione per la CLI companion.

``elo_to_skill_depth``/``STOCKFISH_PATH``/``_parse_companion_move`` sono
importati direttamente da ``backend.main`` (nessuna duplicazione della
tabella ELO→Skill né del parsing SAN/UCI companion) — importare
``backend.main`` non ha side-effect indesiderati all'import (l'app FastAPI
viene costruita ma non avviata: il lifespan gira solo sugli eventi ASGI,
vedi ``backend/main.py``), quindi non serve estrarre un modulo condiviso
separato come ipotizzato in via prudenziale dal design doc.

``backend/main.py`` sa importare sé stesso solo come ``backend.main`` (cwd =
``chess_app/``, convenzione CLAUDE.md/pytest) o come ``main`` bare (cwd =
``chess_app/backend/``, uvicorn lanciato da lì) — NON come
``chess_app.backend.main``. Il modo "ufficiale" di lanciare questa CLI è
``python -m chess_app.cli`` dalla repo root (design doc §11.6), dove invece
``sys.path[0]`` è la repo root: senza intervento, ``import backend`` da lì
fallirebbe. Piuttosto che duplicare/estendere il trucco try/except di
``backend/main.py`` (che avrebbe comunque bisogno di un terzo ramo), ci si
assicura che la directory ``chess_app/`` sia in cima a ``sys.path`` PRIMA di
importare ``backend`` — un fix minimo, locale a questo pacchetto, che rende
``from backend.main import ...`` valido in ENTRAMBI i contesti di lancio
(pytest da ``chess_app/`` è già a posto: l'insert è idempotente).
"""

import os
import sys

_CHESS_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CHESS_APP_DIR not in sys.path:
    sys.path.insert(0, _CHESS_APP_DIR)

from backend.main import STOCKFISH_PATH, _parse_companion_move, elo_to_skill_depth  # noqa: E402

__all__ = [
    "STOCKFISH_PATH",
    "_parse_companion_move",
    "elo_to_skill_depth",
    "BASE_URL",
    "ADVICE_DEPTH",
    "ADVICE_MULTIPV",
    "FULL_STRENGTH_ELO",
]

# Stessa convenzione del frontend (frontend/index.html, costante API): stesso
# backend, stessa porta di default.
BASE_URL = "http://localhost:8765"

# Profondità di ricerca FISSA per il loop di consiglio dal vivo — DISACCOPPIATA
# di proposito dall'effort scelto dall'utente (che governa solo lo Skill
# Level, vedi effort.py). elo_to_skill_depth() associa una depth crescente
# alla forza dell'AVVERSARIO lato backend (fino a 20): non è il parametro
# giusto per la reattività di una REPL dove si digita in fretta e ci si
# aspetta un consiglio quasi istantaneo (design doc §6/§11.2, "da confermare
# in implementazione" — valore fissato qui). 15 è nella fascia 14-16
# suggerita dal design doc: abbastanza profondo da dare un consiglio
# affidabile, abbastanza rapido da non introdurre un lag percepibile in un
# motore già caldo/long-lived (a differenza di /hint via HTTP, che paga
# comunque un popen_uci per chiamata e quindi la sua depth 16 di default non è
# direttamente comparabile in termini di latenza percepita).
ADVICE_DEPTH = 15
ADVICE_MULTIPV = 3

# Soglia oltre la quale l'effort scelto significa "piena forza": il motore
# locale non riceve ALCUNA configurazione di Skill Level (default Stockfish =
# massima forza) invece di uno Skill Level "20" esplicito — coerente con come
# /hint tratta un hint_elo omesso (vedi effort.py:skill_level_for_effort).
FULL_STRENGTH_ELO = 2800
