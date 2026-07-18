"""Costruisce il bundle curato di puzzle tattici (Fase 6, "Modalità puzzle").

Decisione di sourcing (vedi CLAUDE.md, Fase 6): il dataset completo Lichess
(lichess_db_puzzle.csv.zst, ~300 MB compressi / milioni di puzzle, licenza CC0)
è sproporzionato per un'app locale single-user. Questo script scarica UNA VOLTA
una fetta iniziale del file reale via HTTP Range, la decomprime parzialmente,
filtra per qualità, valida ogni puzzle con python-chess e ne campiona un
sottoinsieme stratificato per fascia di rating. L'output è un JSON piccolo
(~100 KB) committato nel repo: a runtime l'app non tocca mai la rete — stesso
precedente di ENDGAME_DRILLS (set statico curato, nessuna dipendenza esterna).

Uso (one-off, richiede rete + `pip install zstandard`, NON in requirements.txt):
    python scripts/build_puzzle_bundle.py

Formato CSV Lichess (una riga per puzzle):
    PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,OpeningTags
La PRIMA mossa di Moves è la mossa avversaria che genera la posizione del
puzzle: qui viene applicata alla FEN in fase di build (campo initial_uci, utile
al frontend per l'highlight), così `fen` nel bundle è già la posizione col
solutore al tratto e `moves` è la sola linea di soluzione (solutore per primo,
lunghezza dispari — l'ultima mossa è sempre del solutore).
"""

import io
import json
import random
import urllib.request
from pathlib import Path

import chess
import zstandard

SOURCE_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
SLICE_BYTES = 12_000_000  # ~12 MB compressi ≈ centinaia di migliaia di righe
OUT_PATH = Path(__file__).resolve().parent.parent / "backend" / "data" / "lichess_puzzles.json"

# Quality filters (Lichess-provided metadata)
MIN_POPULARITY = 90
MIN_PLAYS = 500
MAX_RATING_DEVIATION = 100
MAX_SOLVER_MOVES = 4  # linee più lunghe sono poco adatte al trainer dell'app

# Campionamento stratificato: (etichetta, rating_min, rating_max, quota)
RATING_BANDS = [
    ("beginner", 0, 1200, 120),
    ("intermediate", 1200, 1600, 120),
    ("advanced", 1600, 2000, 100),
    ("expert", 2000, 9999, 60),
]

RNG_SEED = 20260718  # build riproducibile a parità di slice scaricata


def fetch_slice() -> str:
    req = urllib.request.Request(SOURCE_URL, headers={"Range": f"bytes=0-{SLICE_BYTES - 1}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        compressed = resp.read()
    # Truncated frame: decompress as a stream and keep whatever came out.
    dctx = zstandard.ZstdDecompressor()
    out = io.BytesIO()
    reader = dctx.stream_reader(io.BytesIO(compressed))
    try:
        while True:
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    except zstandard.ZstdError:
        pass  # end of the truncated slice — expected
    return out.getvalue().decode("utf-8", errors="ignore")


def validate_and_transform(row: dict) -> dict | None:
    """Valida il puzzle con python-chess e lo trasforma nel formato del bundle.
    Ritorna None se qualunque cosa non torna (mossa illegale, FEN rotta, ...)."""
    try:
        board = chess.Board(row["FEN"])
    except ValueError:
        return None
    uci_moves = row["Moves"].split()
    if len(uci_moves) < 2 or len(uci_moves) % 2 != 0:
        return None  # setup move + linea a lunghezza dispari
    for i, uci in enumerate(uci_moves):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return None
        if move not in board.legal_moves:
            return None
        if i == 0:
            board.push(move)
            fen_after_setup = board.fen()
        else:
            board.push(move)
    solution = uci_moves[1:]
    if (len(solution) + 1) // 2 > MAX_SOLVER_MOVES:
        return None
    return {
        "id": row["PuzzleId"],
        "fen": fen_after_setup,
        "initial_uci": uci_moves[0],
        "moves": solution,
        "rating": int(row["Rating"]),
        "themes": row["Themes"].split(),
        "url": row["GameUrl"],
    }


def main() -> None:
    print(f"Scarico i primi {SLICE_BYTES // 1_000_000} MB di {SOURCE_URL} ...")
    text = fetch_slice()
    lines = text.splitlines()
    header = lines[0].split(",")
    print(f"Decompressi {len(lines) - 1} righe candidate")

    candidates: list[dict] = []
    for line in lines[1:-1]:  # ultima riga probabilmente troncata: scartata
        parts = line.split(",")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        try:
            if (
                int(row["Popularity"]) < MIN_POPULARITY
                or int(row["NbPlays"]) < MIN_PLAYS
                or int(row["RatingDeviation"]) > MAX_RATING_DEVIATION
            ):
                continue
        except (KeyError, ValueError):
            continue
        puzzle = validate_and_transform(row)
        if puzzle is not None:
            candidates.append(puzzle)
    print(f"{len(candidates)} puzzle validi dopo filtri qualità + validazione python-chess")

    rng = random.Random(RNG_SEED)
    selected: list[dict] = []
    for label, lo, hi, quota in RATING_BANDS:
        band = [p for p in candidates if lo <= p["rating"] < hi]
        rng.shuffle(band)
        take = band[:quota]
        print(f"  fascia {label} [{lo}-{hi}): {len(band)} disponibili, presi {len(take)}")
        selected.extend(take)
    selected.sort(key=lambda p: p["rating"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(selected, f, separators=(",", ":"))
    print(f"Scritti {len(selected)} puzzle in {OUT_PATH} ({OUT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
