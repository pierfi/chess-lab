"""Chess Lab — FastAPI backend per giocare e analizzare partite contro Stockfish."""

import io
import math
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

import chess
import chess.engine
import chess.pgn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select

# main.py deve restare importabile sia come ``backend.main`` (test, che girano
# dalla dir chess_app/) sia come ``main`` (uvicorn lanciato da chess_app/backend/,
# vedi CLAUDE.md). Il try/except copre entrambe le invocazioni.
try:
    from backend.db import (
        AnalysisResult,
        ExternalPuzzle,
        Game,
        Move,
        Puzzle,
        SessionLocal,
        SrsCard,
        init_db,
        seed_external_puzzles,
        session_scope,
        utcnow,
    )
    from backend.eco_book import match_opening
except ModuleNotFoundError:  # pragma: no cover - solo per uvicorn da backend/
    from db import (
        AnalysisResult,
        ExternalPuzzle,
        Game,
        Move,
        Puzzle,
        SessionLocal,
        SrsCard,
        init_db,
        seed_external_puzzles,
        session_scope,
        utcnow,
    )
    from eco_book import match_opening


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Comodità stand-alone: crea le tabelle se mancano così l'app parte senza
    # dover lanciare `alembic upgrade head` a mano (WAL/foreign_keys sono
    # applicate per-connessione dall'event listener in db.py). Non eseguito dai
    # test con TestClient(app) senza `with` — lì è conftest.py a creare le tabelle.
    init_db()
    # Semina i puzzle Lichess dal bundle statico (idempotente, no-op se già
    # presenti — vedi db.seed_external_puzzles).
    seed_external_puzzles()
    yield


app = FastAPI(title="Chess Lab", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STOCKFISH_PATH = "/usr/games/stockfish"

# Mapping ELO → (Skill Level, search depth)
ELO_TO_SKILL: list[tuple[int, int, int]] = [
    (800,  0,  1),
    (1000, 3,  3),
    (1200, 6,  5),
    (1400, 9,  7),
    (1600, 12, 9),
    (1800, 15, 12),
    (2000, 18, 15),
]

def elo_to_skill_depth(elo: int) -> tuple[int, int]:
    for threshold, skill, depth in ELO_TO_SKILL:
        if elo < threshold:
            return skill, depth
    return 20, 20

games: dict[str, dict] = {}

class NewGameRequest(BaseModel):
    player_color: str = Field(pattern=r"^(white|black)$")
    engine_elo: int = Field(ge=400, le=2800)
    # Posizione di partenza custom (drill finali, Fase 4). None = partita standard.
    # Non ancora usata da nessun endpoint dedicato: qui viene solo persistita e
    # propagata al chess.Board iniziale quando fornita.
    start_fen: str | None = Field(default=None)

class MoveRequest(BaseModel):
    game_id: str
    move_uci: str

class AnalyzeRequest(BaseModel):
    game_id: str
    depth: int = Field(default=16, ge=1, le=20)

class HintRequest(BaseModel):
    multipv: int = Field(default=3, ge=1, le=5)
    depth: int = Field(default=16, ge=1, le=20)
    # Forza dell'hint engine espressa come ELO (stessa scala dell'avversario).
    # None = piena forza, comportamento storico: nessuno Skill Level configurato.
    hint_elo: int | None = Field(default=None, ge=400, le=2800)

class ImportPgnRequest(BaseModel):
    pgn: str

class PuzzleAnswerRequest(BaseModel):
    move_uci: str

class EndgameStartRequest(BaseModel):
    # Stessi campi di NewGameRequest tranne start_fen: quello viene dal drill
    # scelto (path param), non dal chiamante.
    player_color: str = Field(pattern=r"^(white|black)$")
    engine_elo: int = Field(ge=400, le=2800)

def _new_game_id() -> str:
    return uuid.uuid4().hex[:8]

def _starting_board(start_fen: str | None) -> chess.Board:
    """Board iniziale: standard oppure dalla FEN custom (drill finali)."""
    return chess.Board(start_fen) if start_fen else chess.Board()

def _load_game_from_db(game_id: str) -> dict | None:
    """Cache-miss: ricostruisce la partita dal DB rigiocando gli UCI in ordine
    di ply dalla posizione iniziale (start_fen o standard). Restituisce il dict
    game in-memory pronto per la cache, o None se la riga non esiste."""
    with session_scope() as db:
        row = db.get(Game, game_id)
        if row is None:
            return None
        move_rows = (
            db.execute(
                select(Move).where(Move.game_id == game_id).order_by(Move.ply)
            )
            .scalars()
            .all()
        )
        board = _starting_board(row.start_fen)
        move_objects: list[chess.Move] = []
        for mr in move_rows:
            mv = chess.Move.from_uci(mr.uci)
            board.push(mv)
            move_objects.append(mv)

        # last_engine_move = UCI dell'ultima mossa solo se è dell'engine (cioè
        # se l'ultima riga è del colore avversario al player).
        engine_color = "black" if row.player_color == "white" else "white"
        last_engine_move = None
        if move_rows and move_rows[-1].color == engine_color:
            last_engine_move = move_rows[-1].uci

        return {
            "board": board,
            "player_color": row.player_color,
            "engine_elo": row.engine_elo,
            "move_objects": move_objects,
            "last_engine_move": last_engine_move,
            "created_at": row.created_at.strftime("%Y.%m.%d"),
            "start_fen": row.start_fen,
            # last_ready_at assente: la prima mossa dopo un restart registra
            # think_ms = NULL (comportamento atteso, vedi CLAUDE.md).
        }

def _get_game(game_id: str) -> dict:
    """Write-through cache: hit → oggetto vivo in memoria; miss → ricostruzione
    dal DB (o 404 se assente), poi ripopola la cache."""
    if game_id in games:
        return games[game_id]
    game = _load_game_from_db(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    games[game_id] = game
    return game

def _build_pgn(game: dict) -> str:
    """Costruisce il PGN dalla partita. Condiviso tra la risposta API
    (_board_to_state) e la persistenza (snapshot denormalizzato in games.pgn),
    così la logica non è duplicata. Onora start_fen per i drill da FEN custom."""
    board = game["board"]
    start_fen = game.get("start_fen")
    pgn_game = chess.pgn.Game()
    if start_fen:
        pgn_game.setup(start_fen)
    pgn_game.headers["Event"] = "Chess Lab"
    pgn_game.headers["Date"] = game["created_at"]
    pgn_game.headers["White"] = "Player" if game["player_color"] == "white" else "Stockfish"
    pgn_game.headers["Black"] = "Stockfish" if game["player_color"] == "white" else "Player"
    if board.is_game_over():
        pgn_game.headers["Result"] = board.result()
    node = pgn_game
    for move in game["move_objects"]:
        node = node.add_variation(move)
    return str(pgn_game)

def _current_opening(game: dict, move_history_uci: list[str]) -> dict | None:
    """Apertura ECO corrente via longest-prefix match (Fase 5). Il book è
    costruito sulla posizione di partenza standard: una start_fen custom (drill
    di finali) non ha alcun senso da matchare, quindi niente lookup in quel
    caso."""
    if game.get("start_fen"):
        return None
    return match_opening(move_history_uci)

def _board_to_state(game_id: str, game: dict) -> dict:
    board = game["board"]

    result = None
    if board.is_game_over():
        result = board.result()

    san_history = []
    replay_board = _starting_board(game.get("start_fen"))
    for m in game["move_objects"]:
        san_history.append(replay_board.san(m))
        replay_board.push(m)

    move_history_uci = [m.uci() for m in game["move_objects"]]

    return {
        "game_id": game_id,
        "fen": board.fen(),
        "pgn": _build_pgn(game),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "is_check": board.is_check(),
        "is_game_over": board.is_game_over(),
        "result": result,
        "last_engine_move": game["last_engine_move"],
        "move_history": move_history_uci,
        "move_history_san": san_history,
        "player_color": game["player_color"],
        "engine_elo": game["engine_elo"],
        "opening": _current_opening(game, move_history_uci),
    }

def _engine_move(board: chess.Board, elo: int) -> tuple[chess.Move, float]:
    """Chiede a Stockfish una mossa. Apre e chiude l'engine ad ogni chiamata.

    Impone un tempo minimo di "riflessione" randomizzato: a ELO bassi la ricerca
    è quasi istantanea (depth 1) e la risposta immediata rompe l'illusione di
    giocare contro un avversario. Se l'engine è già lento (depth alte), nessun
    ritardo extra viene aggiunto.

    Ritorna (mossa, elapsed) dove ``elapsed`` è il wall-time REALE della ricerca
    Stockfish, misurato PRIMA del sleep cosmetico. È questo — non il padding —
    che va persistito come think_ms della mossa engine (onestà del dato).
    """
    skill, depth = elo_to_skill_depth(elo)
    target_think = random.uniform(0.6, 1.5)  # seconds
    start = time.monotonic()
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
        engine.configure({"Skill Level": skill})
        result = engine.play(board, chess.engine.Limit(depth=depth))
    elapsed = time.monotonic() - start  # tempo di ricerca reale (esclude il sleep)
    if elapsed < target_think:
        time.sleep(target_think - elapsed)  # padding cosmetico, NON persistito
    return result.move, elapsed

def _check_game_over(board: chess.Board) -> dict | None:
    """Restituisce info game-over o None."""
    if not board.is_game_over():
        return None
    result = board.result()
    reason = "unknown"
    if board.is_checkmate():
        reason = "checkmate"
    elif board.is_stalemate():
        reason = "stalemate"
    elif board.is_insufficient_material():
        reason = "insufficient_material"
    elif board.can_claim_fifty_moves():
        reason = "fifty_moves"
    elif board.can_claim_threefold_repetition():
        reason = "threefold_repetition"
    return {"result": result, "reason": reason}

def _classify(loss_cp: int) -> str:
    if loss_cp >= 200:
        return "blunder"
    if loss_cp >= 80:
        return "mistake"
    if loss_cp >= 30:
        return "inaccuracy"
    if loss_cp >= -10:
        return "good"
    return "excellent"

def _cp_loss_to_move_accuracy(loss_cp: float) -> float:
    """Approssima l'accuracy di una singola mossa dalla perdita in centipawn,
    stile Lichess/chess.com (curva logistica sul win% perso)."""
    loss_cp = max(loss_cp, 0)
    accuracy = 103.1668 * math.exp(-0.04354 * loss_cp) - 3.1669
    return max(0.0, min(100.0, accuracy))

# -------------------------------------------------------------------
# Profilo debolezze (Fase 4, GET /training/weaknesses): euristiche
# python-chess per fase di gioco e tema tattico. Vedi docs/training-mode.md —
# sono APPROSSIMAZIONI ("temi probabili", non un motore tattico completo, per
# esplicita scelta di design: "Cosa NON fare in questa fase").
# -------------------------------------------------------------------

# ply <= soglia ⇒ apertura; oltre, materiale minore/maggiore residuo <= soglia
# (esclusi pedoni e re) ⇒ finale; il resto è mediogioco.
PHASE_OPENING_PLY_MAX = 20
ENDGAME_MATERIAL_THRESHOLD = 13

_PIECE_VALUES = {
    chess.PAWN: 0,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

def _classify_phase(fen_before: str, ply: int) -> str:
    """Fase di gioco al momento della mossa (posizione PRIMA della mossa)."""
    if ply <= PHASE_OPENING_PLY_MAX:
        return "opening"
    board = chess.Board(fen_before)
    material = sum(_PIECE_VALUES[p.piece_type] for p in board.piece_map().values())
    if material <= ENDGAME_MATERIAL_THRESHOLD:
        return "endgame"
    return "middlegame"

def _attacked_enemy_targets(board: chess.Board, square: chess.Square, mover: chess.Color) -> int:
    """Numero di pezzi avversari non-pedone attaccati da ``square`` (usato per
    rilevare un fork: un pezzo che ne minaccia >= 2 dopo la mossa)."""
    enemy = not mover
    n = 0
    for sq in board.attacks(square):
        piece = board.piece_at(sq)
        if piece is not None and piece.color == enemy and piece.piece_type != chess.PAWN:
            n += 1
    return n

def _creates_fork(fen_before: str, move_uci: str, mover: chess.Color) -> bool:
    """True se ``move_uci``, giocata sulla posizione ``fen_before``, porta un
    pezzo che attacca >= 2 pezzi avversari di valore (fork)."""
    board = chess.Board(fen_before)
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return False
    if move not in board.legal_moves:
        return False
    board.push(move)
    return _attacked_enemy_targets(board, move.to_square, mover) >= 2

def _creates_pin(fen_before: str, move_uci: str, mover: chess.Color) -> bool:
    """True se ``move_uci`` crea un NUOVO pin su un pezzo avversario (che non
    esisteva già prima della mossa) — usa board.is_pinned() per-pezzo."""
    board = chess.Board(fen_before)
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return False
    if move not in board.legal_moves:
        return False
    enemy = not mover
    before_pinned = {
        sq for sq, p in board.piece_map().items()
        if p.color == enemy and board.is_pinned(enemy, sq)
    }
    board.push(move)
    after_pinned = {
        sq for sq, p in board.piece_map().items()
        if p.color == enemy and board.is_pinned(enemy, sq)
    }
    return len(after_pinned - before_pinned) > 0

def _king_shield_count(board: chess.Board, color: chess.Color) -> int:
    """Conta i pedoni propri nelle due file/ranghi davanti al re di ``color``
    (scudo pedonale) — proxy grezzo di esposizione del re."""
    king_sq = board.king(color)
    if king_sq is None:
        return 0
    king_file = chess.square_file(king_sq)
    king_rank = chess.square_rank(king_sq)
    direction = 1 if color == chess.WHITE else -1
    count = 0
    for f in (king_file - 1, king_file, king_file + 1):
        if f < 0 or f > 7:
            continue
        for r in (king_rank + direction, king_rank + 2 * direction):
            if r < 0 or r > 7:
                continue
            piece = board.piece_at(chess.square(f, r))
            if piece is not None and piece.color == color and piece.piece_type == chess.PAWN:
                count += 1
    return count

def _exposes_king(fen_before: str, played_uci: str, best_uci: str, mover: chess.Color) -> bool:
    """True se la mossa GIOCATA riduce lo scudo pedonale del proprio re più di
    quanto avrebbe fatto la mossa migliore — approssimazione di "re esposto"."""
    board = chess.Board(fen_before)
    try:
        played_move = chess.Move.from_uci(played_uci)
    except ValueError:
        return False
    if played_move not in board.legal_moves:
        return False
    shield_before = _king_shield_count(board, mover)
    played_board = board.copy()
    played_board.push(played_move)
    shield_played = _king_shield_count(played_board, mover)
    if shield_played >= shield_before:
        return False
    try:
        best_move = chess.Move.from_uci(best_uci)
    except ValueError:
        return True
    if best_move not in board.legal_moves:
        return True
    best_board = board.copy()
    best_board.push(best_move)
    shield_best = _king_shield_count(best_board, mover)
    return shield_played < shield_best

# -------------------------------------------------------------------
# Filtri storico condivisi tra GET /games e gli endpoint /stats/*.
# Fonte unica di verità per la convenzione win/loss/draw (relativa a
# player_color, NON alla stringa PGN grezza) e per i filtri di query.
# -------------------------------------------------------------------
def _result_predicate(result: str | None):
    """Predicato SQL per il filtro result relativo a player_color.
    win → il player ha vinto col suo colore; loss → l'inverso; draw → patta.
    None (o valore ignoto) = nessun filtro."""
    if result == "win":
        return or_(
            and_(Game.player_color == "white", Game.result == "1-0"),
            and_(Game.player_color == "black", Game.result == "0-1"),
        )
    if result == "loss":
        return or_(
            and_(Game.player_color == "white", Game.result == "0-1"),
            and_(Game.player_color == "black", Game.result == "1-0"),
        )
    if result == "draw":
        return Game.result == "1/2-1/2"
    return None

def _player_result(player_color: str, result: str | None) -> str | None:
    """Versione Python di _result_predicate: classifica un singolo esito dal
    punto di vista del player. None se la partita non è decisa (in corso) o se
    l'esito non è uno dei tre canonici. Stessa convenzione, calcolata in-memory
    per gli endpoint che iterano le righe (es. la simulazione ELO)."""
    if result == "1/2-1/2":
        return "draw"
    if (player_color == "white" and result == "1-0") or (
        player_color == "black" and result == "0-1"
    ):
        return "win"
    if result in ("1-0", "0-1"):
        return "loss"
    return None

def _parse_date_range(
    date_from: str | None, date_to: str | None
) -> tuple[datetime | None, datetime | None]:
    """Converte i filtri date (YYYY-MM-DD, su games.created_at) in un intervallo
    [inizio, fine-esclusiva). date_to è inclusiva del giorno intero: internamente
    diventa mezzanotte del giorno dopo (end-exclusive), così una partita giocata
    alle 23:00 del giorno filtrato è compresa. 400 se il formato è errato."""
    dt_from = dt_to = None
    try:
        if date_from is not None:
            dt_from = datetime.combine(date.fromisoformat(date_from), datetime.min.time())
        if date_to is not None:
            dt_to = datetime.combine(
                date.fromisoformat(date_to), datetime.min.time()
            ) + timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date (expected YYYY-MM-DD)")
    return dt_from, dt_to

def _game_filter_conditions(
    color: str | None,
    source: str | None,
    dt_from: datetime | None,
    dt_to: datetime | None,
) -> list:
    """Condizioni WHERE comuni a GET /games e /stats/*: source (default 'play' —
    import e drill esclusi salvo richiesta esplicita), player_color, range date."""
    conds = [Game.source == (source if source is not None else "play")]
    if color is not None:
        conds.append(Game.player_color == color)
    if dt_from is not None:
        conds.append(Game.created_at >= dt_from)
    if dt_to is not None:
        conds.append(Game.created_at < dt_to)
    return conds

# Simulazione ELO (vedi docs/growth-analytics.md). NON è un rating rigoroso: è
# una trend line direzionale. Update Elo classico contro engine_elo come rating
# avversario, K fisso, seed iniziale.
SIM_ELO_SEED = 1200
SIM_ELO_K = 32
SIM_ELO_RECENT_WINDOW = 10

def _elo_expected(player_elo: float, opponent_elo: float) -> float:
    """Punteggio atteso Elo del player contro l'avversario (0..1)."""
    return 1.0 / (1.0 + 10 ** ((opponent_elo - player_elo) / 400.0))

# -------------------------------------------------------------------
# Persistenza (write-through cache): il DB è la fonte durevole, la cache
# in-memory ``games`` resta l'hot path. Vedi db.py per lo schema.
# -------------------------------------------------------------------
def _persist_new_game(
    game_id: str, game: dict, created_at, first_move: dict | None, source: str = "play"
) -> None:
    """Inserisce la riga games alla creazione (+ l'eventuale mossa d'apertura
    dell'engine se il player è nero). ``source`` distingue una partita normale
    ('play', default) da un drill di finali ('endgame_drill', Fase 4)."""
    over = _check_game_over(game["board"])
    with session_scope() as db:
        db.add(Game(
            id=game_id,
            player_color=game["player_color"],
            engine_elo=game["engine_elo"],
            start_fen=game.get("start_fen"),
            source=source,
            pgn=_build_pgn(game),
            created_at=created_at,
            result=over["result"] if over else None,
            result_reason=over["reason"] if over else None,
            finished_at=created_at if over else None,
        ))
        if first_move is not None:
            db.add(Move(game_id=game_id, created_at=created_at, **first_move))

def _persist_move_batch(game_id: str, game: dict, pending: list[dict], over: dict | None) -> None:
    """Persiste le righe moves prodotte da una richiesta /game/move e aggiorna
    lo snapshot denormalizzato (pgn) + gli esiti di fine partita, in una sola
    sessione. Se la riga games manca (partita creata fuori da /game/new) la
    crea difensivamente, così ogni mossa finisce comunque nel DB durevole."""
    now = utcnow()
    with session_scope() as db:
        row = db.get(Game, game_id)
        if row is None:
            row = Game(
                id=game_id,
                player_color=game["player_color"],
                engine_elo=game["engine_elo"],
                start_fen=game.get("start_fen"),
                source="play",
                created_at=now,
            )
            db.add(row)
        for mv in pending:
            db.add(Move(game_id=game_id, created_at=now, **mv))
        row.pgn = _build_pgn(game)
        if over:
            row.result = over["result"]
            row.result_reason = over["reason"]
            if row.finished_at is None:
                row.finished_at = now

def _persist_analysis(
    game_id: str,
    analysis_moves: list[dict],
    accuracy: float,
    blunders: int,
    mistakes: int,
    inaccuracies: int,
) -> None:
    """Upsert dei risultati di /game/analyze in ``analysis_results`` (unique su
    game_id+ply: ri-analizzare la stessa partita aggiorna le righe esistenti,
    non le duplica) + aggiornamento delle colonne di riepilogo su ``games`` così
    una game-list view può mostrare lo stato di analisi senza ri-interrogare
    analysis_results.

    Difensivo: se la riga games non esiste (es. una partita iniettata solo in
    cache, come nei test che bypassano /game/new) non scrive nulla — un insert
    su analysis_results fallirebbe comunque la FK con foreign_keys=ON."""
    now = utcnow()
    with session_scope() as db:
        game_row = db.get(Game, game_id)
        if game_row is None:
            return

        existing = {
            row.ply: row
            for row in db.execute(
                select(AnalysisResult).where(AnalysisResult.game_id == game_id)
            ).scalars().all()
        }
        for m in analysis_moves:
            row = existing.get(m["ply"])
            if row is None:
                row = AnalysisResult(game_id=game_id, ply=m["ply"])
                db.add(row)
            row.classification = m["classification"]
            row.loss_cp = m["loss_cp"]
            row.score_cp = m["score_cp"]
            row.best_move_uci = m["best_move_uci"]
            row.is_mate_swing = m["is_mate_swing"]

        game_row.analyzed_at = now
        game_row.player_accuracy = accuracy
        game_row.blunders = blunders
        game_row.mistakes = mistakes
        game_row.inaccuracies = inaccuracies


@app.get("/health")
def health():
    return {"status": "ok"}

def _create_new_game(req: NewGameRequest, source: str = "play") -> dict:
    """Core di creazione partita, condiviso da /game/new e dal drill di finali
    (POST /training/endgames/{id}/start, Fase 4) — stessa logica, diverso
    ``source`` persistito e diversa provenienza di ``start_fen``."""
    game_id = _new_game_id()

    # Board iniziale: standard oppure da start_fen (validata qui per non far
    # esplodere in un 500 se la FEN è malformata).
    try:
        board = _starting_board(req.start_fen)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start_fen")

    created_at = utcnow()
    game = {
        "board": board,
        "player_color": req.player_color,
        "engine_elo": req.engine_elo,
        "move_objects": [],
        "last_engine_move": None,
        "created_at": created_at.strftime("%Y.%m.%d"),
        "start_fen": req.start_fen,
    }

    # Turno iniziale dedotto dalla board: bianco per la posizione standard, ma
    # può essere nero per una start_fen custom (es. drill di finali con nero al
    # tratto). Se non coincide col colore scelto dal player, l'engine apre la
    # partita con quel colore — generalizza il vecchio controllo hardcoded
    # "player_color == black ⇒ ply 1 bianco", corretto solo per la posizione
    # standard e mai esercitato finora da uno start_fen non standard.
    initial_turn = "white" if board.turn == chess.WHITE else "black"
    first_move = None
    if req.player_color != initial_turn:
        fen_before = board.fen()
        engine_m, elapsed = _engine_move(board, req.engine_elo)
        san = board.san(engine_m)  # SAN calcolata PRIMA del push
        board.push(engine_m)
        game["move_objects"].append(engine_m)
        game["last_engine_move"] = engine_m.uci()
        first_move = {
            "ply": 1,
            "color": initial_turn,
            "uci": engine_m.uci(),
            "san": san,
            "fen_before": fen_before,
            "think_ms": round(elapsed * 1000),
        }

    games[game_id] = game
    _persist_new_game(game_id, game, created_at, first_move, source=source)

    # Marker per il think time della prossima mossa del player.
    game["last_ready_at"] = time.monotonic()
    return _board_to_state(game_id, game)

@app.post("/game/new")
def new_game(req: NewGameRequest):
    return _create_new_game(req, source="play")

@app.post("/game/move")
def make_move(req: MoveRequest):
    game = _get_game(req.game_id)
    board = game["board"]

    if board.is_game_over():
        raise HTTPException(status_code=400, detail="Game is already over")

    # Verifica turno del player
    player_turn = chess.WHITE if game["player_color"] == "white" else chess.BLACK
    if board.turn != player_turn:
        raise HTTPException(status_code=400, detail="Not your turn")

    # Parse e validazione mossa (supporta promozione es. e7e8q)
    try:
        move = chess.Move.from_uci(req.move_uci)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UCI format")

    if move not in board.legal_moves:
        raise HTTPException(status_code=400, detail="Illegal move")

    # Think time del player: da quando la risposta precedente gli ha ridato il
    # turno (marker last_ready_at) fino ad ora. Assente dopo un restart → NULL.
    last_ready = game.get("last_ready_at")
    player_think_ms = (
        round((time.monotonic() - last_ready) * 1000) if last_ready is not None else None
    )

    player_color = game["player_color"]
    engine_color = "black" if player_color == "white" else "white"
    pending: list[dict] = []

    # Esegui mossa player (SAN e fen_before catturati PRIMA del push)
    player_fen_before = board.fen()
    player_san = board.san(move)
    board.push(move)
    game["move_objects"].append(move)
    game["last_engine_move"] = None
    pending.append({
        "ply": len(game["move_objects"]),
        "color": player_color,
        "uci": move.uci(),
        "san": player_san,
        "fen_before": player_fen_before,
        "think_ms": player_think_ms,
    })

    # Controlla game-over dopo mossa player
    over = _check_game_over(board)
    if over:
        _persist_move_batch(req.game_id, game, pending, over)
        state = _board_to_state(req.game_id, game)
        state["game_over"] = over
        game["last_ready_at"] = time.monotonic()
        return state

    # Mossa Stockfish (think_ms = wall-time reale della ricerca, no padding)
    engine_fen_before = board.fen()
    engine_m, elapsed = _engine_move(board, game["engine_elo"])
    engine_san = board.san(engine_m)
    board.push(engine_m)
    game["move_objects"].append(engine_m)
    game["last_engine_move"] = engine_m.uci()
    pending.append({
        "ply": len(game["move_objects"]),
        "color": engine_color,
        "uci": engine_m.uci(),
        "san": engine_san,
        "fen_before": engine_fen_before,
        "think_ms": round(elapsed * 1000),
    })

    # Controlla game-over dopo mossa engine
    over = _check_game_over(board)
    _persist_move_batch(req.game_id, game, pending, over)
    state = _board_to_state(req.game_id, game)
    if over:
        state["game_over"] = over
    game["last_ready_at"] = time.monotonic()
    return state

@app.get("/game/{game_id}")
def get_game(game_id: str):
    game = _get_game(game_id)
    return _board_to_state(game_id, game)

@app.post("/game/{game_id}/hint")
def game_hint(game_id: str, req: HintRequest):
    """Analisi live per il gioco assistito: best move, eval e mosse candidate
    (MultiPV) sulla posizione corrente, senza toccare lo stato della partita."""
    game = _get_game(game_id)
    board = game["board"]

    if board.is_game_over():
        raise HTTPException(status_code=400, detail="Game is already over")

    # Hint-engine separato dal play-engine, indipendente dall'ELO scelto per la
    # partita. Default (hint_elo omesso): piena forza, nessuno Skill Level
    # configurato — comportamento storico invariato. Con hint_elo il suggerimento
    # viene calibrato al livello richiesto riusando lo stesso mapping ELO→Skill
    # dell'avversario (solo lo Skill Level: la depth resta governata da req.depth).
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
        if req.hint_elo is not None:
            skill, _ = elo_to_skill_depth(req.hint_elo)
            engine.configure({"Skill Level": skill})
        infos = engine.analyse(board, chess.engine.Limit(depth=req.depth), multipv=req.multipv)

    # Con multipv impostato analyse() ritorna una lista di info (anche per multipv=1)
    lines = []
    for info in infos:
        pv = info.get("pv")
        if not pv:
            continue
        move = pv[0]
        score_pov_white = info["score"].white()
        if score_pov_white.is_mate():
            cp_white = 10000 if score_pov_white.mate() > 0 else -10000
        else:
            cp_white = score_pov_white.score()
        lines.append({
            "move_uci": move.uci(),
            "move_san": board.san(move),  # SAN sulla posizione corrente, nessun push
            "score_cp": cp_white,
        })

    # MultiPV ritorna già le linee ordinate per forza; riordino esplicito perché
    # lines[0] deve essere la migliore per chi muove (score bianco decrescente se
    # muove il bianco, crescente se muove il nero).
    lines.sort(key=lambda line: line["score_cp"], reverse=(board.turn == chess.WHITE))

    return {
        "eval_cp": lines[0]["score_cp"],
        "lines": lines,
    }

# Valori convenzionali dei pezzi per l'endpoint /threats (P1 N/B3 R5 Q9, per
# ordinare/enfatizzare). Distinti da _PIECE_VALUES (soglia materiale di fase,
# dove pedoni e re valgono 0 per definizione).
_PRESA_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}

@app.get("/game/{game_id}/threats")
def game_threats(game_id: str):
    """Pezzi "in presa" del lato al tratto: attaccati da almeno un pezzo
    avversario E non difesi da nessun proprio pezzo (definizione v1, livello 2
    di docs/threatened-pieces-design.md). Funzione pura della posizione
    corrente calcolata con python-chess — MAI Stockfish: il valore della
    feature è essere sempre-aggiornabile a costo quasi zero, disaccoppiata
    dal /hint on-demand. Non muta stato, non tocca il DB."""
    game = _get_game(game_id)
    board = game["board"]

    if board.is_game_over():
        raise HTTPException(status_code=400, detail="Game is already over")

    me = board.turn
    opponent = not me
    in_presa = []
    for sq, piece in board.piece_map().items():
        # Il re non entra mai: se attaccato è scacco, già coperto da .king-check
        if piece.color != me or piece.piece_type == chess.KING:
            continue
        attackers = board.attackers(opponent, sq)
        if not attackers:
            continue
        if board.attackers(me, sq):
            continue  # defended (even once): not hanging in the v1 definition
        in_presa.append({
            "square": chess.square_name(sq),
            "piece": piece.symbol(),
            "value": _PRESA_PIECE_VALUES[piece.piece_type],
            "attackers": sorted(chess.square_name(a) for a in attackers),
        })

    # Pezzi di maggior valore per primi (ordinamento deterministico)
    in_presa.sort(key=lambda entry: (-entry["value"], entry["square"]))

    return {
        "side": "white" if me == chess.WHITE else "black",
        "in_presa": in_presa,
    }

@app.post("/game/analyze")
def analyze_game(req: AnalyzeRequest):
    game = _get_game(req.game_id)
    board = _starting_board(game.get("start_fen"))
    # Turno iniziale dedotto dalla board (bianco per la posizione standard,
    # ma può essere nero per una start_fen custom, es. drill Philidor) —
    # stesso pattern di _create_new_game, serve per attribuire move_number
    # correttamente quando il ply 1 non è del Bianco (Bug #9).
    initial_turn = "white" if board.turn == chess.WHITE else "black"
    moves = game["move_objects"]

    if not moves:
        raise HTTPException(status_code=400, detail="No moves to analyze")

    analysis_moves = []
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
        # Una search per ogni posizione PRIMA di ciascuna mossa, PIÙ una per la
        # posizione finale dopo l'ultima mossa (N+1 search totali, non 2N): serve
        # sia l'eval "prima" che l'eval "dopo" di ogni mossa per attribuire la loss
        # alla mossa giusta — eval-dopo-mossa-i == eval-prima-mossa-(i+1), quindi
        # ogni posizione viene comunque analizzata una sola volta.
        boards_before = []
        scratch_board = _starting_board(game.get("start_fen"))
        for move in moves:
            boards_before.append(scratch_board.copy())
            scratch_board.push(move)
        boards_before.append(scratch_board.copy())  # posizione finale

        cp_scores: list[int] = []
        is_mate_flags: list[bool] = []
        pvs: list[list] = []
        for b in boards_before:
            info = engine.analyse(b, chess.engine.Limit(depth=req.depth))
            score_pov_white = info["score"].white()
            is_mate = score_pov_white.is_mate()
            if is_mate:
                mate_in = score_pov_white.mate()
                cp_scores.append(10000 if mate_in > 0 else -10000)
            else:
                cp_scores.append(score_pov_white.score())
            is_mate_flags.append(is_mate)
            pvs.append(info.get("pv") or [])

        for ply_idx, move in enumerate(moves):
            cp_before = cp_scores[ply_idx]
            cp_after = cp_scores[ply_idx + 1]
            pv = pvs[ply_idx]
            best_move_uci = pv[0].uci() if pv else None

            # SAN della mossa giocata (calcolata PRIMA di push)
            move_san = board.san(move)
            color = "white" if board.turn == chess.WHITE else "black"

            # Loss = eval-prima-della-mossa vs eval-dopo-della-mossa, dal punto
            # di vista di chi ha mosso (score positivo = bene per il bianco).
            if color == "white":
                loss_cp = cp_before - cp_after
            else:
                loss_cp = cp_after - cp_before

            # Classificazione sul loss NON clampato (un matto mancato/concesso
            # deve comunque leggersi come blunder)
            classification = _classify(loss_cp)

            # Mate swing: matto presente prima o dopo questa mossa.
            # Clamp del loss numerico per display sano (il frontend può usare
            # is_mate_swing per mostrare "Mate!" al posto del cp grezzo).
            is_mate_swing = is_mate_flags[ply_idx] or is_mate_flags[ply_idx + 1]
            if is_mate_swing:
                loss_cp = max(-1000, min(1000, loss_cp))

            # Linea migliore (fino a 4 plies in SAN) solo per blunder/mistake
            best_line_san = None
            if classification in ("blunder", "mistake") and pv:
                scratch = board.copy()
                best_line_san = []
                for pv_move in pv[:4]:
                    best_line_san.append(scratch.san(pv_move))
                    scratch.push(pv_move)

            # move_number: raggruppa i ply in coppie Bianco/Nero per la tabella
            # a due colonne del frontend. La formula classica (ply_idx//2 + 1)
            # assume ply 1 = Bianco; per uno start_fen col Nero al tratto si
            # applica un offset di un ply "virtuale" così il primo ply (Nero)
            # resta da solo nella riga 1 e il Bianco apre la riga 2 (stesso
            # pattern di _create_new_game: turno iniziale da board.turn, non
            # hardcoded Bianco-first — Bug #9).
            effective_ply = ply_idx if initial_turn == "white" else ply_idx + 1
            analysis_moves.append({
                "ply": ply_idx + 1,
                "move_number": (effective_ply // 2) + 1,
                "color": color,
                "move_uci": move.uci(),
                "move_san": move_san,
                "best_move_uci": best_move_uci,
                "score_cp": cp_after,
                "loss_cp": loss_cp,
                "classification": classification,
                "is_mate_swing": is_mate_swing,
                "best_line_san": best_line_san,
            })

            board.push(move)

    # Statistiche
    player_color = game["player_color"]
    player_moves = [m for m in analysis_moves if m["color"] == player_color]
    blunders = sum(1 for m in player_moves if m["classification"] == "blunder")
    mistakes = sum(1 for m in player_moves if m["classification"] == "mistake")
    inaccuracies = sum(1 for m in player_moves if m["classification"] == "inaccuracy")

    # Accuracy: media delle accuracy per-mossa dal cp loss (curva logistica
    # stile Lichess/chess.com), non un semplice conteggio good-or-better
    if player_moves:
        accuracy = sum(
            _cp_loss_to_move_accuracy(m["loss_cp"]) for m in player_moves
        ) / len(player_moves)
    else:
        accuracy = 0

    accuracy_score = round(accuracy, 1)

    # Persistenza additiva: la risposta al chiamante resta identica a prima,
    # questa è solo la scrittura durevole in analysis_results + il riepilogo
    # su games (vedi _persist_analysis per i dettagli di upsert/idempotenza).
    _persist_analysis(req.game_id, analysis_moves, accuracy_score, blunders, mistakes, inaccuracies)

    return {
        "game_id": req.game_id,
        "total_moves": len(moves),
        "blunders": blunders,
        "mistakes": mistakes,
        "inaccuracies": inaccuracies,
        "accuracy_score": accuracy_score,
        "moves": analysis_moves,
    }

@app.get("/games")
def list_games(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    color: str | None = Query(default=None, pattern=r"^(white|black)$"),
    result: str | None = Query(default=None, pattern=r"^(win|loss|draw)$"),
    source: str | None = Query(default=None),
):
    """Lista paginata/filtrata delle partite dal DB (non dalla cache in-memory,
    così funziona anche per partite non attualmente cache-hot). ``result`` è
    relativo a ``player_color`` (non la stringa PGN grezza): win/loss/draw dal
    punto di vista del giocatore. Default ``source``: solo 'play' — i drill di
    finali e gli import restano fuori dallo storico partite di default."""
    with session_scope() as db:
        stmt = select(Game).where(
            *_game_filter_conditions(color, source, None, None)
        )
        pred = _result_predicate(result)
        if pred is not None:
            stmt = stmt.where(pred)

        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()

        rows = db.execute(
            stmt.order_by(Game.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).scalars().all()

        # Move count in blocco (una query sola per l'intera pagina, non N+1).
        game_ids = [row.id for row in rows]
        move_counts: dict[str, int] = {}
        if game_ids:
            move_counts = dict(
                db.execute(
                    select(Move.game_id, func.count(Move.id))
                    .where(Move.game_id.in_(game_ids))
                    .group_by(Move.game_id)
                ).all()
            )

        items = [
            {
                "game_id": row.id,
                "created_at": row.created_at.isoformat(),
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                "player_color": row.player_color,
                "engine_elo": row.engine_elo,
                "result": row.result,
                "result_reason": row.result_reason,
                "move_count": move_counts.get(row.id, 0),
                "analyzed_at": row.analyzed_at.isoformat() if row.analyzed_at else None,
                "player_accuracy": row.player_accuracy,
                "blunders": row.blunders,
                "mistakes": row.mistakes,
                "inaccuracies": row.inaccuracies,
            }
            for row in rows
        ]

    return {"items": items, "page": page, "per_page": per_page, "total": total}

@app.get("/game/{game_id}/replay")
def game_replay(game_id: str):
    """Sequenza di FEN per il replay. Usa moves.fen_before (già persistito per
    ply, vedi Fase 3) per ogni posizione intermedia — nessuna ri-simulazione —
    più la posizione finale, ricostruita da _get_game (stessa logica di
    cache-hit/miss condivisa con GET /game/{id}, non duplicata qui)."""
    game = _get_game(game_id)  # 404 se non esiste, gestisce anche il cache-miss
    with session_scope() as db:
        move_rows = (
            db.execute(select(Move).where(Move.game_id == game_id).order_by(Move.ply))
            .scalars()
            .all()
        )
        moves = [
            {"ply": m.ply, "uci": m.uci, "san": m.san, "think_ms": m.think_ms}
            for m in move_rows
        ]
        fens = [m.fen_before for m in move_rows]

    fens.append(game["board"].fen())
    opening = _current_opening(game, [m["uci"] for m in moves])
    return {"fens": fens, "moves": moves, "pgn": _build_pgn(game), "opening": opening}

@app.delete("/game/{game_id}")
def delete_game(game_id: str):
    """Cancella la partita: la riga games + cascade DB (moves/analysis_results/
    puzzles/srs_cards, ON DELETE CASCADE con foreign_keys=ON, vedi db.py) e
    l'eviction dalla cache in-memory, così una richiesta in-flight non può
    resuscitare una partita appena cancellata leggendola dalla cache."""
    with session_scope() as db:
        row = db.get(Game, game_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Game not found")
        db.delete(row)
    games.pop(game_id, None)
    return {"deleted": True, "game_id": game_id}

@app.post("/games/import")
def import_game(req: ImportPgnRequest):
    """Importa una partita da PGN esterno (source='import'). Rigioca la
    mainline in una board fresca, persistendo una riga moves per ply (stesso
    shape del loop live: color/uci/san/fen_before, ma think_ms=NULL — nessun
    dato di timing reale per una partita non giocata qui). Nessuna analisi
    automatica: resta una chiamata esplicita e separata a /game/analyze.

    Convenzioni per un import (nessun vero "player" locale in una partita
    esterna, ma games.player_color/engine_elo non sono nullable):
    - player_color: sempre 'white'. Puramente convenzionale — determina solo a
      quale lato /game/analyze attribuisce blunder/mistake/accuracy se lo si
      analizza in seguito.
    - engine_elo: sentinella 0 ("avversario sconosciuto/importato"), scelta
      invece di NULL per non alterare lo schema Fase 1 (colonna NOT NULL)."""
    parsed = chess.pgn.read_game(io.StringIO(req.pgn))
    if parsed is None or parsed.errors:
        raise HTTPException(status_code=400, detail="Invalid PGN")

    start_fen = None
    try:
        board = chess.Board(parsed.headers["FEN"]) if parsed.headers.get("FEN") else chess.Board()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start FEN in PGN headers")
    if parsed.headers.get("FEN"):
        start_fen = board.fen()

    move_objects: list[chess.Move] = []
    move_rows: list[dict] = []
    ply = 0
    for move in parsed.mainline_moves():
        ply += 1
        if move not in board.legal_moves:
            raise HTTPException(status_code=400, detail=f"Illegal move at ply {ply} in PGN")
        fen_before = board.fen()
        san = board.san(move)
        color = "white" if board.turn == chess.WHITE else "black"
        board.push(move)
        move_objects.append(move)
        move_rows.append({
            "ply": ply,
            "color": color,
            "uci": move.uci(),
            "san": san,
            "fen_before": fen_before,
            "think_ms": None,
        })

    # chess.pgn.read_game() è tollerante: testo non-PGN produce comunque un
    # Game valido (senza errors) ma a zero mosse — è così che rileviamo un
    # input spazzatura/vuoto, non tramite `parsed.errors` (spesso vuoto anche
    # per garbage in input).
    if not move_rows:
        raise HTTPException(status_code=400, detail="PGN contains no moves")

    game_id = _new_game_id()
    created_at = utcnow()
    player_color = "white"
    engine_elo = 0  # sentinella "avversario sconosciuto" per un import, vedi docstring

    game = {
        "board": board,
        "player_color": player_color,
        "engine_elo": engine_elo,
        "move_objects": move_objects,
        "last_engine_move": None,
        "created_at": created_at.strftime("%Y.%m.%d"),
        "start_fen": start_fen,
    }

    over = _check_game_over(board)
    with session_scope() as db:
        db.add(Game(
            id=game_id,
            player_color=player_color,
            engine_elo=engine_elo,
            start_fen=start_fen,
            source="import",
            pgn=_build_pgn(game),
            created_at=created_at,
            result=over["result"] if over else None,
            result_reason=over["reason"] if over else None,
            finished_at=created_at if over else None,
        ))
        for mv in move_rows:
            db.add(Move(game_id=game_id, created_at=created_at, **mv))

    games[game_id] = game

    state = _board_to_state(game_id, game)
    state["source"] = "import"
    return state

@app.get("/stats/summary")
def stats_summary(
    color: str | None = Query(default=None, pattern=r"^(white|black)$"),
    source: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
):
    """Numeri di riepilogo su tutto lo storico persistito (filtrabile per colore,
    range date su created_at, source). Convenzioni condivise con GET /games:
    win/loss/draw relativi a player_color, source default 'play'.

    - I *tassi* win/loss/draw sono relativi alle sole partite DECISE (con result
      non nullo): le partite in corso non hanno esito e non devono diluire il
      denominatore.
    - avg_accuracy media games.player_accuracy solo sulle partite ANALIZZATE
      (analyzed_at IS NOT NULL): le non analizzate non hanno accuracy e vanno
      escluse, non contate come 0.
    - avg_think_ms_per_move è calcolata sulle sole mosse DEL PLAYER (color ==
      player_color della partita) con think_ms non nullo — riflette il tempo di
      riflessione dell'utente, non quello dell'engine."""
    dt_from, dt_to = _parse_date_range(date_from, date_to)
    conds = _game_filter_conditions(color, source, dt_from, dt_to)

    with session_scope() as db:
        rows = db.execute(select(Game).where(*conds)).scalars().all()

        total_games = len(rows)
        wins = losses = draws = 0
        for row in rows:
            outcome = _player_result(row.player_color, row.result)
            if outcome == "win":
                wins += 1
            elif outcome == "loss":
                losses += 1
            elif outcome == "draw":
                draws += 1
        decided = wins + losses + draws

        analyzed = [r for r in rows if r.analyzed_at is not None]
        avg_accuracy = (
            round(sum(r.player_accuracy for r in analyzed) / len(analyzed), 1)
            if analyzed
            else None
        )
        total_blunders = sum(r.blunders or 0 for r in analyzed)
        total_mistakes = sum(r.mistakes or 0 for r in analyzed)
        total_inaccuracies = sum(r.inaccuracies or 0 for r in analyzed)

        # Think time medio sulle sole mosse del player (join moves↔games sugli
        # stessi filtri, color della mossa == player_color della partita).
        avg_think_ms_row = db.execute(
            select(func.avg(Move.think_ms))
            .select_from(Move)
            .join(Game, Move.game_id == Game.id)
            .where(
                *conds,
                Move.color == Game.player_color,
                Move.think_ms.isnot(None),
            )
        ).scalar_one()
        avg_think_ms = round(avg_think_ms_row) if avg_think_ms_row is not None else None

    def _rate(n: int) -> float:
        return round(n / decided, 3) if decided else 0.0

    return {
        "total_games": total_games,
        "decided_games": decided,
        "analyzed_games": len(analyzed),
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": _rate(wins),
        "loss_rate": _rate(losses),
        "draw_rate": _rate(draws),
        "avg_accuracy": avg_accuracy,
        "total_blunders": total_blunders,
        "total_mistakes": total_mistakes,
        "total_inaccuracies": total_inaccuracies,
        "avg_think_ms_per_move": avg_think_ms,
    }

@app.get("/stats/progress")
def stats_progress(
    color: str | None = Query(default=None, pattern=r"^(white|black)$"),
    source: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
):
    """Serie temporale per il grafico di crescita (frontend Fase 3). Un punto per
    partita DECISA, in ordine cronologico (created_at asc). Ogni punto porta un
    ELO simulato — proxy direzionale del miglioramento, NON un rating rigoroso.

    Algoritmo (vedi docs/growth-analytics.md): update Elo classico partita-per-
    partita, con engine_elo come rating avversario e result (relativo a
    player_color) come esito. K fisso, seed iniziale; il rating è riportato DOPO
    l'applicazione di ogni partita. Le partite in corso (result nullo) sono
    saltate; source default 'play' esclude gli import (engine_elo=0 sentinella li
    renderebbe inutilizzabili come avversario)."""
    dt_from, dt_to = _parse_date_range(date_from, date_to)
    conds = _game_filter_conditions(color, source, dt_from, dt_to)

    with session_scope() as db:
        rows = db.execute(
            select(Game).where(*conds).order_by(Game.created_at.asc(), Game.id.asc())
        ).scalars().all()

        series = []
        current = float(SIM_ELO_SEED)
        peak = float(SIM_ELO_SEED)
        game_number = 0
        for row in rows:
            outcome = _player_result(row.player_color, row.result)
            if outcome is None:
                continue  # partita in corso / esito non canonico: non conteggiata
            score = {"win": 1.0, "draw": 0.5, "loss": 0.0}[outcome]
            expected = _elo_expected(current, row.engine_elo)
            current = current + SIM_ELO_K * (score - expected)
            peak = max(peak, current)
            game_number += 1
            series.append({
                "game_id": row.id,
                "date": row.created_at.isoformat(),
                "game_number": game_number,
                "engine_elo": row.engine_elo,
                "result": outcome,
                "score": score,
                "simulated_elo": round(current),
                "accuracy": (
                    round(row.player_accuracy, 1)
                    if row.player_accuracy is not None
                    else None
                ),
            })

    # Finestra recente: variazione ELO e accuracy media sulle ultime N partite.
    window = SIM_ELO_RECENT_WINDOW
    recent_slice = series[-window:]
    if recent_slice:
        pre_idx = len(series) - len(recent_slice) - 1
        pre_elo = series[pre_idx]["simulated_elo"] if pre_idx >= 0 else SIM_ELO_SEED
        recent_accs = [p["accuracy"] for p in recent_slice if p["accuracy"] is not None]
        recent = {
            "window": window,
            "games": len(recent_slice),
            "elo_change": recent_slice[-1]["simulated_elo"] - pre_elo,
            "avg_accuracy": (
                round(sum(recent_accs) / len(recent_accs), 1) if recent_accs else None
            ),
            "wins": sum(1 for p in recent_slice if p["result"] == "win"),
            "losses": sum(1 for p in recent_slice if p["result"] == "loss"),
            "draws": sum(1 for p in recent_slice if p["result"] == "draw"),
        }
    else:
        recent = {
            "window": window,
            "games": 0,
            "elo_change": 0,
            "avg_accuracy": None,
            "wins": 0,
            "losses": 0,
            "draws": 0,
        }

    return {
        "seed_elo": SIM_ELO_SEED,
        "k_factor": SIM_ELO_K,
        "games_counted": len(series),
        "current_elo": series[-1]["simulated_elo"] if series else SIM_ELO_SEED,
        "peak_elo": round(peak),
        "series": series,
        "recent": recent,
    }

# =====================================================================
# Fase 4 — Allenamento mirato (docs/training-mode.md)
# =====================================================================

def _puzzle_to_dict(puzzle: Puzzle) -> dict:
    """Shape condivisa tra la risposta di generazione e quella di ripasso.
    ``player_to_move`` è derivato dal campo attivo del FEN, non ricalcolato
    altrove."""
    player_to_move = "white" if puzzle.fen.split()[1] == "w" else "black"
    return {
        "puzzle_id": puzzle.id,
        "game_id": puzzle.game_id,
        "ply": puzzle.ply,
        "fen": puzzle.fen,
        "player_to_move": player_to_move,
        "source": puzzle.source,
    }

@app.get("/training/puzzles/next")
def next_puzzle(source: str | None = Query(default=None)):
    """Prossima carta da ripassare (SRS, scaduta) oppure, se la coda è vuota,
    un nuovo puzzle generato dal blunder/mistake più recente non ancora
    trasformato in carta (fallback a inaccuracy se non ce ne sono — vedi
    tabella rischi in docs/training-mode.md). Il puzzle prende il FEN da
    moves.fen_before allo stesso ply (già persistito, nessuna ri-simulazione).

    ``source`` è opzionale (default: nessun filtro, comportamento storico) —
    limita la generazione di NUOVI puzzle alle partite con quel games.source.
    Non filtra la coda di ripasso (i puzzle già generati restano ripassabili
    a prescindere dalla partita di origine). Utile soprattutto per isolare i
    test dallo storico condiviso, sullo stesso pattern di GET /games."""
    now = utcnow()
    with session_scope() as db:
        due = db.execute(
            select(SrsCard, Puzzle)
            .join(Puzzle, SrsCard.puzzle_id == Puzzle.id)
            .where(SrsCard.due_at.isnot(None), SrsCard.due_at <= now)
            .order_by(SrsCard.due_at.asc())
            .limit(1)
        ).first()
        if due is not None:
            card, puzzle = due
            resp = _puzzle_to_dict(puzzle)
            resp["is_review"] = True
            resp["due_at"] = card.due_at.isoformat()
            return resp

        # Coda vuota: genera un nuovo puzzle. Un blunder/mistake è "candidato"
        # se non esiste già una riga puzzles per lo stesso (game_id, ply) —
        # altrimenti rigenereremmo puzzle duplicati ad ogni chiamata.
        row = None
        for classes in (["blunder", "mistake"], ["inaccuracy"]):
            already_puzzled = select(Puzzle.id).where(
                Puzzle.game_id == AnalysisResult.game_id,
                Puzzle.ply == AnalysisResult.ply,
            )
            conds = [
                AnalysisResult.classification.in_(classes),
                AnalysisResult.best_move_uci.isnot(None),
                Move.color == Game.player_color,
                ~already_puzzled.exists(),
            ]
            if source is not None:
                conds.append(Game.source == source)
            stmt = (
                select(AnalysisResult, Move.fen_before)
                .join(
                    Move,
                    and_(AnalysisResult.game_id == Move.game_id, AnalysisResult.ply == Move.ply),
                )
                .join(Game, AnalysisResult.game_id == Game.id)
                .where(*conds)
                .order_by(Game.created_at.desc(), AnalysisResult.ply.desc())
                .limit(1)
            )
            result = db.execute(stmt).first()
            if result is not None:
                row = result
                break

        if row is None:
            return {
                "puzzle_id": None,
                "message": "Nessuna carta in scadenza e nessun blunder/mistake nuovo da trasformare in puzzle.",
            }

        analysis_row, fen_before = row
        puzzle = Puzzle(
            game_id=analysis_row.game_id,
            ply=analysis_row.ply,
            fen=fen_before,
            best_move_uci=analysis_row.best_move_uci,
            source=analysis_row.classification,
        )
        db.add(puzzle)
        db.flush()  # popola puzzle.id senza attendere il commit di uscita
        resp = _puzzle_to_dict(puzzle)
        resp["is_review"] = False
        resp["due_at"] = None
        return resp

@app.post("/training/puzzles/{puzzle_id}/answer")
def answer_puzzle(puzzle_id: int, req: PuzzleAnswerRequest):
    """Valida move_uci contro best_move_uci (match esatto, nessuna tolleranza
    cp — puzzle a soluzione unica) e aggiorna lo scheduling SM-2 semplificato
    (docs/training-mode.md). La carta SRS nasce qui al primo tentativo, non
    alla generazione del puzzle."""
    now = utcnow()
    with session_scope() as db:
        puzzle = db.get(Puzzle, puzzle_id)
        if puzzle is None:
            raise HTTPException(status_code=404, detail="Puzzle not found")

        card = db.execute(
            select(SrsCard).where(SrsCard.puzzle_id == puzzle_id)
        ).scalar_one_or_none()
        if card is None:
            # Valori espliciti (non i default di colonna, applicati solo
            # all'INSERT): servono subito qui perché li aggiorniamo prima del
            # flush/commit di fine sessione.
            card = SrsCard(puzzle_id=puzzle_id, ease_factor=2.5, correct_streak=0)
            db.add(card)

        correct = req.move_uci.strip().lower() == puzzle.best_move_uci.lower()

        if correct:
            card.correct_streak += 1
            if card.correct_streak == 1:
                card.interval_days = 1
            elif card.correct_streak == 2:
                card.interval_days = 3
            else:
                card.interval_days = round((card.interval_days or 1) * card.ease_factor)
            card.ease_factor = min(card.ease_factor + 0.1, 3.0)
        else:
            card.correct_streak = 0
            card.interval_days = 1
            card.ease_factor = max(card.ease_factor - 0.2, 1.3)

        card.due_at = now + timedelta(days=card.interval_days)
        card.last_reviewed_at = now

        result = {
            "correct": correct,
            "best_move_uci": puzzle.best_move_uci,
            "next_due_at": card.due_at.isoformat(),
            "interval_days": card.interval_days,
            "ease_factor": round(card.ease_factor, 2),
            "correct_streak": card.correct_streak,
        }

    return result

@app.get("/training/weaknesses")
def training_weaknesses(source: str | None = Query(default=None)):
    """Aggregazione errori del PLAYER (non dell'engine) per fase di gioco e
    tema tattico probabile. Euristiche approssimate (python-chess, nessun
    motore tattico dedicato) — vedi docs/training-mode.md: da presentare come
    suggerimento, non come diagnosi certa. ``source`` default 'play', stessa
    convenzione di GET /games e /stats/*."""
    src = source if source is not None else "play"

    with session_scope() as db:
        rows = db.execute(
            select(AnalysisResult, Move.fen_before, Move.uci, Move.color)
            .join(
                Move,
                and_(AnalysisResult.game_id == Move.game_id, AnalysisResult.ply == Move.ply),
            )
            .join(Game, AnalysisResult.game_id == Game.id)
            .where(Game.source == src, Move.color == Game.player_color)
        ).all()

    phase_stats = {p: {"total_loss": 0.0, "count": 0} for p in ("opening", "middlegame", "endgame")}
    theme_counts = {"fork": 0, "pin": 0, "king_safety": 0}

    for ar, fen_before, move_uci, color in rows:
        mover = chess.WHITE if color == "white" else chess.BLACK

        phase = _classify_phase(fen_before, ar.ply)
        phase_stats[phase]["total_loss"] += ar.loss_cp
        phase_stats[phase]["count"] += 1

        if ar.classification in ("blunder", "mistake") and ar.best_move_uci:
            if _creates_fork(fen_before, ar.best_move_uci, mover) and not _creates_fork(
                fen_before, move_uci, mover
            ):
                theme_counts["fork"] += 1
            if _creates_pin(fen_before, ar.best_move_uci, mover) and not _creates_pin(
                fen_before, move_uci, mover
            ):
                theme_counts["pin"] += 1
            if _exposes_king(fen_before, move_uci, ar.best_move_uci, mover):
                theme_counts["king_safety"] += 1

    by_phase = {
        phase: {
            "avg_loss_cp": round(stats["total_loss"] / stats["count"], 1) if stats["count"] else None,
            "count": stats["count"],
        }
        for phase, stats in phase_stats.items()
    }
    by_theme = {theme: {"missed_count": n} for theme, n in theme_counts.items()}

    return {
        "by_phase": by_phase,
        "by_theme": by_theme,
        "note": "Euristiche approssimate su python-chess: temi probabili, non diagnosi certa.",
    }

# -------------------------------------------------------------------
# Drill di finali teorici (set statico, ~15-20 posizioni canoniche). Ogni
# voce: fen, goal ("win"|"draw"), description breve. Stockfish a piena forza
# funge da "tablebase" didattica sufficiente (vedi docs/training-mode.md).
# -------------------------------------------------------------------
ENDGAME_DRILLS: list[dict] = [
    {
        "id": "kq_vs_k",
        "name": "Re e Donna contro Re",
        "fen": "4k3/8/8/8/8/8/8/3QK3 w - - 0 1",
        "goal": "win",
        "description": "Scaccomatto elementare: guida il re avversario sul bordo con la donna.",
    },
    {
        "id": "kr_vs_k",
        "name": "Re e Torre contro Re",
        "fen": "4k3/8/8/8/8/8/8/R3K3 w - - 0 1",
        "goal": "win",
        "description": "Scaccomatto elementare con la tecnica della scala (torre + re).",
    },
    {
        "id": "k2r_vs_k",
        "name": "Re e due Torri contro Re",
        "fen": "4k3/8/8/8/8/8/8/R3K2R w - - 0 1",
        "goal": "win",
        "description": "Matto della scala con due torri: il più semplice dei finali di matto.",
    },
    {
        "id": "two_bishops_vs_k",
        "name": "Due Alfieri contro Re",
        "fen": "4k3/8/8/8/8/8/8/2B1KB2 w - - 0 1",
        "goal": "win",
        "description": "Matto con due alfieri: tecnica della gabbia diagonale, più delicata del matto con torre.",
    },
    {
        "id": "bn_vs_k",
        "name": "Alfiere e Cavallo contro Re (avanzato)",
        "fen": "4k3/8/8/8/8/8/8/2BNK3 w - - 0 1",
        "goal": "win",
        "description": "Il finale di matto più difficile tra i 4 elementari: matto forzato in un angolo del colore dell'alfiere.",
    },
    {
        "id": "kp_opposition_win",
        "name": "Opposizione Re e Pedone (vincente)",
        "fen": "8/8/4k3/8/4P3/4K3/8/8 w - - 0 1",
        "goal": "win",
        "description": "Il re bianco ha già l'opposizione: il pedone promuove con la tecnica corretta.",
    },
    {
        "id": "kp_draw",
        "name": "Opposizione Re e Pedone (patta)",
        "fen": "8/8/8/8/8/4k3/4p3/4K3 b - - 0 1",
        "goal": "draw",
        "description": "Il re difensore ha l'opposizione: il pedone non passa, la posizione è patta con gioco corretto.",
    },
    {
        "id": "lucena",
        "name": "Posizione di Lucena",
        "fen": "1K1k4/1P6/8/8/8/8/r7/5R2 w - - 0 1",
        "goal": "win",
        "description": "Il finale di torre più famoso: la tecnica del 'ponte' costruisce un riparo per il re e vince.",
    },
    {
        "id": "philidor",
        "name": "Posizione di Philidor",
        "fen": "8/8/8/3k4/8/3K4/3P4/3r4 b - - 0 1",
        "goal": "draw",
        "description": "Difesa sulla sesta traversa: il lato debole tiene la patta tenendo la torre sulla riga davanti al pedone.",
    },
    {
        "id": "q_vs_p_7th",
        "name": "Donna contro pedone in settima",
        "fen": "8/8/8/8/8/2k5/1p6/1K1Q4 w - - 0 1",
        "goal": "win",
        "description": "La donna da sola batte un pedone avanzato (non di torre/alfiere) prossimo alla promozione.",
    },
    {
        "id": "r_vs_b_draw",
        "name": "Torre contro Alfiere (patta)",
        "fen": "8/8/8/4k3/8/4b3/8/R3K3 w - - 0 1",
        "goal": "draw",
        "description": "Materiale spaiato classico: il lato debole tiene la patta con difesa corretta.",
    },
    {
        "id": "r_vs_n_draw",
        "name": "Torre contro Cavallo (patta)",
        "fen": "8/8/8/4k3/8/4n3/8/R3K3 w - - 0 1",
        "goal": "draw",
        "description": "Come torre-vs-alfiere: patta teorica se il cavallo resta vicino al proprio re.",
    },
    {
        "id": "outside_passed",
        "name": "Pedone passato lontano",
        "fen": "8/8/1p6/1P6/8/2k5/6P1/2K5 w - - 0 1",
        "goal": "win",
        "description": "Il pedone passato sull'ala opposta distrae il re avversario: il pedone di riserva decide.",
    },
    {
        "id": "q_vs_r",
        "name": "Donna contro Torre",
        "fen": "8/8/8/4k3/8/8/4r3/3QK3 w - - 0 1",
        "goal": "win",
        "description": "Finale tecnico classico: la donna vince contro la torre isolata con la tecnica corretta.",
    },
    {
        "id": "trebuchet",
        "name": "Trébuchet (zugzwang reciproco)",
        "fen": "8/8/3k4/3p4/3P4/3K4/8/8 w - - 0 1",
        "goal": "draw",
        "description": "Pedoni bloccati, re a contatto: chi deve muovere perde l'opposizione — qui è patta.",
    },
    {
        "id": "rook_pawn_win",
        "name": "Torre contro Re, pedone di torre",
        "fen": "8/6k1/8/8/8/8/R5K1/8 w - - 0 1",
        "goal": "win",
        "description": "Variante del matto elementare con re avversario già spinto verso il bordo lungo.",
    },
]

_ENDGAME_DRILLS_BY_ID = {d["id"]: d for d in ENDGAME_DRILLS}

@app.get("/training/endgames")
def list_endgames():
    """Lista statica dei drill di finali teorici disponibili."""
    return {"endgames": ENDGAME_DRILLS}

@app.post("/training/endgames/{endgame_id}/start")
def start_endgame(endgame_id: str, req: EndgameStartRequest):
    """Avvia una partita da un FEN custom (drill di finali) — estende
    POST /game/new con start_fen preso dal drill selezionato (non dal
    chiamante). Riusa _create_new_game così mosse/game-over/PGN restano
    l'infrastruttura esistente, nessuna duplicazione."""
    drill = _ENDGAME_DRILLS_BY_ID.get(endgame_id)
    if drill is None:
        raise HTTPException(status_code=404, detail="Endgame drill not found")

    new_req = NewGameRequest(
        player_color=req.player_color,
        engine_elo=req.engine_elo,
        start_fen=drill["fen"],
    )
    state = _create_new_game(new_req, source="endgame_drill")
    state["endgame_id"] = endgame_id
    state["goal"] = drill["goal"]
    return state

# =====================================================================
# Fase 6 — Modalità puzzle (dataset Lichess esterno, bundle statico).
# Sistema DISTINTO dai puzzle self-generated di Fase 4 (/training/puzzles):
# tabella dedicata external_puzzles, nessuna FK verso games, nessun SRS.
# Validazione STATELESS: il client manda (puzzle_id, move_index, move_uci),
# il server ricostruisce la posizione dalla soluzione persistita — nessuno
# stato di sessione lato server, nessuna scrittura DB.
# =====================================================================

class ExternalPuzzleAnswerRequest(BaseModel):
    # Indice della mossa nella linea di soluzione (0-based, pari = tocca al
    # solutore: le mosse dispari sono le risposte avversarie auto-giocate).
    move_index: int = Field(default=0, ge=0)
    move_uci: str

def _external_puzzle_to_dict(pz: ExternalPuzzle) -> dict:
    """Shape pubblica del puzzle: la soluzione NON viene mai esposta qui —
    solo la sua lunghezza (in mosse del solutore) per il progresso UI."""
    solution = pz.moves_uci.split()
    return {
        "puzzle_id": pz.id,
        "fen": pz.fen,
        "initial_uci": pz.initial_uci,
        "player_to_move": "white" if pz.fen.split()[1] == "w" else "black",
        "rating": pz.rating,
        "themes": pz.themes.split(),
        "solution_moves": (len(solution) + 1) // 2,
        "lichess_url": pz.lichess_url,
    }

@app.get("/puzzles/next")
def next_external_puzzle(
    theme: str | None = Query(default=None),
    min_rating: int | None = Query(default=None, ge=0),
    max_rating: int | None = Query(default=None, ge=0),
    exclude: str | None = Query(default=None),
):
    """Puzzle casuale dal bundle Lichess, filtrabile per tema e fascia di
    rating. ``exclude`` (id dell'ultimo puzzle mostrato) evita la ripetizione
    immediata quando più di un puzzle soddisfa i filtri. Selezione con
    ORDER BY RANDOM(): a ~400 righe il costo è irrilevante."""
    conds = []
    if theme is not None:
        # themes è spazio-separata: padding con spazi per il match a parola
        # intera (evita che "pin" catturi "kingsideAttack" o "pinning").
        conds.append(
            func.instr(" " + ExternalPuzzle.themes + " ", f" {theme} ") > 0
        )
    if min_rating is not None:
        conds.append(ExternalPuzzle.rating >= min_rating)
    if max_rating is not None:
        conds.append(ExternalPuzzle.rating <= max_rating)

    with session_scope() as db:
        stmt = select(ExternalPuzzle).where(*conds)
        if exclude is not None:
            excluded = stmt.where(ExternalPuzzle.id != exclude)
            pz = db.execute(
                excluded.order_by(func.random()).limit(1)
            ).scalar_one_or_none()
            # exclude è best-effort: se l'unico match è proprio quello escluso,
            # meglio riproporlo che rispondere "nessun puzzle".
            if pz is None:
                pz = db.execute(
                    stmt.order_by(func.random()).limit(1)
                ).scalar_one_or_none()
        else:
            pz = db.execute(
                stmt.order_by(func.random()).limit(1)
            ).scalar_one_or_none()

        if pz is None:
            return {
                "puzzle_id": None,
                "message": "Nessun puzzle corrisponde ai filtri selezionati.",
            }
        return _external_puzzle_to_dict(pz)

@app.get("/puzzles/themes")
def external_puzzle_themes():
    """Temi disponibili nel bundle con conteggio, per il filtro frontend.
    Aggregazione in Python: ~400 righe, nessun bisogno di SQL elaborato."""
    with session_scope() as db:
        rows = db.execute(select(ExternalPuzzle.themes)).scalars().all()
    counts: dict[str, int] = {}
    for themes in rows:
        for t in themes.split():
            counts[t] = counts.get(t, 0) + 1
    return {
        "themes": [
            {"theme": t, "count": n}
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
    }

@app.post("/puzzles/{puzzle_id}/answer")
def answer_external_puzzle(puzzle_id: str, req: ExternalPuzzleAnswerRequest):
    """Valida la mossa del solutore all'indice ``move_index`` della linea di
    soluzione. Stateless: la posizione viene ricostruita dalla FEN + il
    prefisso di soluzione già giocato (bundle pre-validato in build, il replay
    non può fallire). Regola Lichess: un MATTO immediato alternativo alla
    mossa attesa è comunque corretto (e completa il puzzle).

    Se corretta e la linea non è finita, la risposta include la contromossa
    avversaria (reply) e ``next_fen`` — il client non deve applicare mosse a
    una FEN da solo. Se sbagliata il puzzle è fallito: ``expected_uci`` è
    sempre presente per mostrare la soluzione del passo corrente."""
    with session_scope() as db:
        pz = db.get(ExternalPuzzle, puzzle_id)
        if pz is None:
            raise HTTPException(status_code=404, detail="Puzzle not found")
        solution = pz.moves_uci.split()
        fen = pz.fen

    if req.move_index >= len(solution) or req.move_index % 2 != 0:
        raise HTTPException(status_code=400, detail="Invalid move_index")

    board = chess.Board(fen)
    for uci in solution[:req.move_index]:
        board.push(chess.Move.from_uci(uci))

    try:
        played = chess.Move.from_uci(req.move_uci.strip().lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UCI format")
    if played not in board.legal_moves:
        raise HTTPException(status_code=400, detail="Illegal move")

    expected_uci = solution[req.move_index]
    expected = chess.Move.from_uci(expected_uci)

    alternate_mate = False
    if played != expected:
        probe = board.copy()
        probe.push(played)
        alternate_mate = probe.is_checkmate()

    correct = played == expected or alternate_mate
    played_san = board.san(played)

    if not correct:
        return {
            "correct": False,
            "completed": False,
            "solved_by_alternate_mate": False,
            "expected_uci": expected_uci,
            "played_san": played_san,
            "reply_uci": None,
            "reply_san": None,
            "next_fen": None,
            "next_move_index": None,
        }

    board.push(played)
    completed = alternate_mate or req.move_index + 1 >= len(solution)
    reply_uci = reply_san = None
    next_move_index = None
    if not completed:
        reply_uci = solution[req.move_index + 1]
        reply = chess.Move.from_uci(reply_uci)
        reply_san = board.san(reply)
        board.push(reply)
        next_move_index = req.move_index + 2

    return {
        "correct": True,
        "completed": completed,
        "solved_by_alternate_mate": alternate_mate,
        "expected_uci": expected_uci,
        "played_san": played_san,
        "reply_uci": reply_uci,
        "reply_san": reply_san,
        "next_fen": board.fen(),
        "next_move_index": next_move_index,
    }
