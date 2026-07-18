"""Identificazione apertura ECO dal prefisso di mosse della partita (Fase 5).

Dataset statico in ``data/eco.json`` (~680 aperture/varianti note, curate a
partire dal dataset pubblico lichess-org/chess-openings, validate con
python-chess — vedi CLAUDE.md Fase 5 per i dettagli di provenienza). Nessuna
dipendenza dal DB: puro lookup in memoria, caricato una volta all'import.
"""

import json
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "eco.json"


def _load_book() -> dict[tuple[str, ...], dict]:
    with open(_DATA_PATH, encoding="utf-8") as fh:
        entries = json.load(fh)
    return {tuple(e["uci"]): {"eco": e["eco"], "name": e["name"]} for e in entries}


_BOOK: dict[tuple[str, ...], dict] = _load_book()
_MAX_PLY: int = max((len(k) for k in _BOOK), default=0)


def match_opening(move_history_uci: list[str]) -> dict | None:
    """Longest-prefix match contro il book: la riga nota più lunga il cui
    elenco di mosse è un prefisso esatto della cronologia data. None se
    nessuna riga corrisponde (posizione fuori libro fin dalla prima mossa, o
    la partita è già divergente da ogni variante nota)."""
    upper = min(len(move_history_uci), _MAX_PLY)
    for k in range(upper, 0, -1):
        hit = _BOOK.get(tuple(move_history_uci[:k]))
        if hit is not None:
            return hit
    return None
