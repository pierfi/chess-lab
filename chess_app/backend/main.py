"""Chess Lab — FastAPI backend per giocare e analizzare partite contro Stockfish."""

import io
import math
import random
import time
import uuid
from contextlib import asynccontextmanager

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
        Game,
        Move,
        SessionLocal,
        init_db,
        session_scope,
        utcnow,
    )
except ModuleNotFoundError:  # pragma: no cover - solo per uvicorn da backend/
    from db import (
        AnalysisResult,
        Game,
        Move,
        SessionLocal,
        init_db,
        session_scope,
        utcnow,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Comodità stand-alone: crea le tabelle se mancano così l'app parte senza
    # dover lanciare `alembic upgrade head` a mano (WAL/foreign_keys sono
    # applicate per-connessione dall'event listener in db.py). Non eseguito dai
    # test con TestClient(app) senza `with` — lì è conftest.py a creare le tabelle.
    init_db()
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

class ImportPgnRequest(BaseModel):
    pgn: str

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

    return {
        "game_id": game_id,
        "fen": board.fen(),
        "pgn": _build_pgn(game),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "is_check": board.is_check(),
        "is_game_over": board.is_game_over(),
        "result": result,
        "last_engine_move": game["last_engine_move"],
        "move_history": [m.uci() for m in game["move_objects"]],
        "move_history_san": san_history,
        "player_color": game["player_color"],
        "engine_elo": game["engine_elo"],
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
# Persistenza (write-through cache): il DB è la fonte durevole, la cache
# in-memory ``games`` resta l'hot path. Vedi db.py per lo schema.
# -------------------------------------------------------------------
def _persist_new_game(game_id: str, game: dict, created_at, first_move: dict | None) -> None:
    """Inserisce la riga games alla creazione (+ l'eventuale mossa d'apertura
    dell'engine se il player è nero)."""
    over = _check_game_over(game["board"])
    with session_scope() as db:
        db.add(Game(
            id=game_id,
            player_color=game["player_color"],
            engine_elo=game["engine_elo"],
            start_fen=game.get("start_fen"),
            source="play",
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

@app.post("/game/new")
def new_game(req: NewGameRequest):
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

    # Se il player è nero, Stockfish gioca per primo (ply 1, colore bianco).
    first_move = None
    if req.player_color == "black":
        fen_before = board.fen()
        engine_m, elapsed = _engine_move(board, req.engine_elo)
        san = board.san(engine_m)  # SAN calcolata PRIMA del push
        board.push(engine_m)
        game["move_objects"].append(engine_m)
        game["last_engine_move"] = engine_m.uci()
        first_move = {
            "ply": 1,
            "color": "white",
            "uci": engine_m.uci(),
            "san": san,
            "fen_before": fen_before,
            "think_ms": round(elapsed * 1000),
        }

    games[game_id] = game
    _persist_new_game(game_id, game, created_at, first_move)

    # Marker per il think time della prossima mossa del player.
    game["last_ready_at"] = time.monotonic()
    return _board_to_state(game_id, game)

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

    # Hint-engine separato dal play-engine, a piena forza: nessuno Skill Level
    # configurato, indipendente dall'ELO scelto per la partita.
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
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

@app.post("/game/analyze")
def analyze_game(req: AnalyzeRequest):
    game = _get_game(req.game_id)
    board = chess.Board()
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
        scratch_board = chess.Board()
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

            analysis_moves.append({
                "ply": ply_idx + 1,
                "move_number": (ply_idx // 2) + 1,
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
        stmt = select(Game)
        stmt = stmt.where(Game.source == (source if source is not None else "play"))
        if color is not None:
            stmt = stmt.where(Game.player_color == color)
        if result == "win":
            stmt = stmt.where(
                or_(
                    and_(Game.player_color == "white", Game.result == "1-0"),
                    and_(Game.player_color == "black", Game.result == "0-1"),
                )
            )
        elif result == "loss":
            stmt = stmt.where(
                or_(
                    and_(Game.player_color == "white", Game.result == "0-1"),
                    and_(Game.player_color == "black", Game.result == "1-0"),
                )
            )
        elif result == "draw":
            stmt = stmt.where(Game.result == "1/2-1/2")

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
    return {"fens": fens, "moves": moves, "pgn": _build_pgn(game)}

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
