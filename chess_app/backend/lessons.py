"""Lezioni di teoria (Fase 4 — Allenamento) — contenuto statico curato a mano.

Set piccolo e curato, stessa filosofia di ENDGAME_DRILLS/eco_book.py: nessun
DB, nessuna dipendenza esterna, caricato una volta all'import. Dataset in
``data/lessons.json`` (bare array — un JSON perché ogni lezione porta prosa
multi-frase, a differenza dei one-liner di ENDGAME_DRILLS). Vedi
docs/theory-lessons-design.md §3/§4/§5.

Convenzione di authoring (§8 Q5, accettata): la linea di ogni lezione è
autorata in SAN (leggibile per chi scrive i contenuti). Questo modulo la
rigioca con python-chess a partire da ``start_fen``, derivando sia l'UCI per
ogni step sia la sequenza di FEN (N+1 posizioni, stesso contratto di
GET /game/{id}/replay). La validazione di legalità avviene qui, al
caricamento del modulo: una mossa SAN malformata o illegale fa fallire
l'import subito (fail loudly), non una risposta 200 con FEN rotte a runtime.
"""

import json
from pathlib import Path

import chess

_DATA_PATH = Path(__file__).parent / "data" / "lessons.json"


def _expand_lesson(raw: dict) -> dict:
    """Rigioca raw['line'] (SAN) da raw['start_fen'], derivando uci/fens.
    Solleva ValueError (con lesson id e mossa incriminata) se la linea non è
    legale — non deve mai succedere per contenuto curato, ma se succede deve
    esplodere in fase di caricamento/test, non servire una FEN silenziosamente
    sbagliata al client."""
    lesson_id = raw.get("id", "<unknown>")
    board = chess.Board(raw["start_fen"])
    fens = [board.fen()]
    line = []
    for step in raw["line"]:
        san = step["san"]
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            raise ValueError(
                f"Lezione '{lesson_id}': mossa SAN '{san}' non legale/valida "
                f"alla posizione {board.fen()}"
            ) from exc
        uci = move.uci()
        board.push(move)
        fens.append(board.fen())

        entry = {
            "ply": len(line) + 1,
            "uci": uci,
            "san": san,
            "mode": step["mode"],
            "comment": step.get("comment", ""),
        }
        if step["mode"] == "play":
            entry["prompt"] = step.get("prompt", "")
        line.append(entry)

    return {
        "id": raw["id"],
        "title": raw["title"],
        "category": raw["category"],
        "level": raw["level"],
        "orientation": raw["orientation"],
        "summary": raw["summary"],
        "intro": raw["intro"],
        "start_fen": raw["start_fen"],
        "related_drill_id": raw.get("related_drill_id"),
        "fens": fens,
        "line": line,
    }


def _load_lessons() -> list[dict]:
    with open(_DATA_PATH, encoding="utf-8") as fh:
        raw_lessons = json.load(fh)
    return [_expand_lesson(raw) for raw in raw_lessons]


# Espansione + validazione fatte una sola volta all'import, non per-request:
# lo stesso identico spirito di ENDGAME_DRILLS/eco_book._BOOK. Il file cresce
# via merge di più fragment (agenti diversi ne autorano porzioni separate,
# vedi docs/theory-lessons-design.md) quindi qui NON si assume un numero fisso
# di lezioni.
LESSONS: list[dict] = _load_lessons()
_LESSONS_BY_ID: dict[str, dict] = {lesson["id"]: lesson for lesson in LESSONS}


def list_lessons() -> list[dict]:
    """Metadati soli (id/title/category/level/summary), per GET
    /training/lessons — MAI la linea/fens completa, che è nel dettaglio."""
    return [
        {
            "id": lesson["id"],
            "title": lesson["title"],
            "category": lesson["category"],
            "level": lesson["level"],
            "summary": lesson["summary"],
        }
        for lesson in LESSONS
    ]


def get_lesson(lesson_id: str) -> dict | None:
    """Dettaglio completo (già espanso) per GET /training/lessons/{id}, o
    None se l'id non esiste (404 a carico del chiamante)."""
    return _LESSONS_BY_ID.get(lesson_id)
