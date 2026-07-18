"""Configurazione pytest per Chess Lab.

Isola la persistenza su un DB SQLite temporaneo: la env var CHESS_LAB_DB va
impostata PRIMA di importare backend.db/backend.main (che leggono il percorso a
import-time), quindi qui, al top-level del conftest della rootdir (caricato da
pytest prima dei moduli di test).
"""

import atexit
import os
import tempfile

_fd, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db", prefix="chesslab_test_")
os.close(_fd)
os.environ["CHESS_LAB_DB"] = _TEST_DB_PATH

# Import DOPO aver settato la env var, così l'engine punta al DB temporaneo.
from backend.db import init_db, seed_external_puzzles  # noqa: E402

init_db()  # crea lo schema (stesse tabelle della migration Alembic iniziale)
seed_external_puzzles()  # bundle puzzle Lichess (Fase 6), come nel lifespan


@atexit.register
def _cleanup_test_db():
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_TEST_DB_PATH + suffix)
        except OSError:
            pass
