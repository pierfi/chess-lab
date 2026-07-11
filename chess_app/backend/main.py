"""Chess Lab — FastAPI backend per giocare e analizzare partite contro Stockfish."""

import math
import random
import time
import uuid
from datetime import date

import chess
import chess.engine
import chess.pgn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Chess Lab", version="0.1.0")
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

def _new_game_id() -> str:
    return uuid.uuid4().hex[:8]

def _get_game(game_id: str) -> dict:
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    return games[game_id]

def _board_to_state(game_id: str, game: dict) -> dict:
    board = game["board"]
    # Build PGN
    pgn_game = chess.pgn.Game()
    pgn_game.headers["Event"] = "Chess Lab"
    pgn_game.headers["Date"] = game["created_at"]
    pgn_game.headers["White"] = "Player" if game["player_color"] == "white" else "Stockfish"
    pgn_game.headers["Black"] = "Stockfish" if game["player_color"] == "white" else "Player"
    if board.is_game_over():
        pgn_game.headers["Result"] = board.result()
    node = pgn_game
    for move in game["move_objects"]:
        node = node.add_variation(move)

    result = None
    if board.is_game_over():
        result = board.result()

    san_history = []
    replay_board = chess.Board()
    for m in game["move_objects"]:
        san_history.append(replay_board.san(m))
        replay_board.push(m)

    return {
        "game_id": game_id,
        "fen": board.fen(),
        "pgn": str(pgn_game),
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

def _engine_move(board: chess.Board, elo: int) -> chess.Move:
    """Chiede a Stockfish una mossa. Apre e chiude l'engine ad ogni chiamata.

    Impone un tempo minimo di "riflessione" randomizzato: a ELO bassi la ricerca
    è quasi istantanea (depth 1) e la risposta immediata rompe l'illusione di
    giocare contro un avversario. Se l'engine è già lento (depth alte), nessun
    ritardo extra viene aggiunto.
    """
    skill, depth = elo_to_skill_depth(elo)
    target_think = random.uniform(0.6, 1.5)  # seconds
    start = time.monotonic()
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
        engine.configure({"Skill Level": skill})
        result = engine.play(board, chess.engine.Limit(depth=depth))
    elapsed = time.monotonic() - start
    if elapsed < target_think:
        time.sleep(target_think - elapsed)
    return result.move

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

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/game/new")
def new_game(req: NewGameRequest):
    game_id = _new_game_id()
    board = chess.Board()
    game = {
        "board": board,
        "player_color": req.player_color,
        "engine_elo": req.engine_elo,
        "move_objects": [],
        "last_engine_move": None,
        "created_at": date.today().strftime("%Y.%m.%d"),
    }

    # Se il player è nero, Stockfish gioca per primo
    if req.player_color == "black":
        engine_m = _engine_move(board, req.engine_elo)
        board.push(engine_m)
        game["move_objects"].append(engine_m)
        game["last_engine_move"] = engine_m.uci()

    games[game_id] = game
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

    # Esegui mossa player
    board.push(move)
    game["move_objects"].append(move)
    game["last_engine_move"] = None

    # Controlla game-over dopo mossa player
    over = _check_game_over(board)
    if over:
        state = _board_to_state(req.game_id, game)
        state["game_over"] = over
        return state

    # Mossa Stockfish
    engine_m = _engine_move(board, game["engine_elo"])
    board.push(engine_m)
    game["move_objects"].append(engine_m)
    game["last_engine_move"] = engine_m.uci()

    # Controlla game-over dopo mossa engine
    state = _board_to_state(req.game_id, game)
    over = _check_game_over(board)
    if over:
        state["game_over"] = over
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

    return {
        "game_id": req.game_id,
        "total_moves": len(moves),
        "blunders": blunders,
        "mistakes": mistakes,
        "inaccuracies": inaccuracies,
        "accuracy_score": round(accuracy, 1),
        "moves": analysis_moves,
    }
