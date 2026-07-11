"""Test end-to-end per Chess Lab API."""

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import app, games
from backend.db import SessionLocal, Game, Move


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def white_game(client):
    """Crea una partita con player bianco, ELO basso."""
    r = client.post("/game/new", json={"player_color": "white", "engine_elo": 800})
    assert r.status_code == 200
    data = r.json()
    assert data["game_id"]
    assert data["turn"] == "white"
    return data


@pytest.fixture
def black_game(client):
    """Crea una partita con player nero — Stockfish muove per primo."""
    r = client.post("/game/new", json={"player_color": "black", "engine_elo": 800})
    assert r.status_code == 200
    data = r.json()
    # Stockfish ha già mosso (bianco), ora tocca al nero
    assert data["turn"] == "black"
    assert len(data["move_history"]) == 1
    return data


# -------------------------------------------------------------------
# /game/new
# -------------------------------------------------------------------
class TestNewGame:
    def test_new_game_white(self, white_game):
        assert white_game["player_color"] == "white"
        assert white_game["is_game_over"] is False
        assert "is_check" in white_game
        assert "move_history_san" in white_game

    def test_new_game_black(self, black_game):
        assert black_game["player_color"] == "black"
        assert black_game["last_engine_move"] is not None
        assert len(black_game["move_history_san"]) == 1

    def test_invalid_color(self, client):
        r = client.post("/game/new", json={"player_color": "red", "engine_elo": 800})
        assert r.status_code == 422

    def test_elo_out_of_range(self, client):
        r = client.post("/game/new", json={"player_color": "white", "engine_elo": 100})
        assert r.status_code == 422
        r = client.post("/game/new", json={"player_color": "white", "engine_elo": 3000})
        assert r.status_code == 422


# -------------------------------------------------------------------
# /game/move
# -------------------------------------------------------------------
class TestMakeMove:
    def test_legal_move(self, client, white_game):
        r = client.post("/game/move", json={
            "game_id": white_game["game_id"],
            "move_uci": "e2e4",
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data["move_history"]) == 2
        assert len(data["move_history_san"]) == 2
        assert data["move_history_san"][0] == "e4"
        assert data["last_engine_move"] is not None

    def test_illegal_move(self, client, white_game):
        r = client.post("/game/move", json={
            "game_id": white_game["game_id"],
            "move_uci": "e2e5",  # illegale
        })
        assert r.status_code == 400

    def test_invalid_uci_format(self, client, white_game):
        r = client.post("/game/move", json={
            "game_id": white_game["game_id"],
            "move_uci": "xyz",
        })
        assert r.status_code == 400

    def test_game_not_found(self, client):
        r = client.post("/game/move", json={
            "game_id": "nonexist",
            "move_uci": "e2e4",
        })
        assert r.status_code == 404


# -------------------------------------------------------------------
# /game/{id}
# -------------------------------------------------------------------
class TestGetGame:
    def test_get_existing(self, client, white_game):
        r = client.get(f"/game/{white_game['game_id']}")
        assert r.status_code == 200
        assert r.json()["fen"] == white_game["fen"]

    def test_get_not_found(self, client):
        r = client.get("/game/nonexist")
        assert r.status_code == 404


# -------------------------------------------------------------------
# /game/analyze
# -------------------------------------------------------------------
class TestAnalyze:
    def test_analyze_after_moves(self, client, white_game):
        gid = white_game["game_id"]
        # Gioca qualche mossa
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        client.post("/game/move", json={"game_id": gid, "move_uci": "d2d4"})

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        data = r.json()
        assert data["total_moves"] >= 4
        assert "moves" in data
        # Verifica che move_san sia SAN e non UCI
        for m in data["moves"]:
            assert m["move_san"]
            # SAN non contiene cifre consecutive come UCI (es. "e2e4")
            assert not (len(m["move_san"]) == 4 and m["move_san"][1].isdigit() and m["move_san"][3].isdigit()), \
                f"move_san looks like UCI: {m['move_san']}"

    def test_analyze_no_moves(self, client):
        r = client.post("/game/new", json={"player_color": "white", "engine_elo": 800})
        gid = r.json()["game_id"]
        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 400

    def test_analyze_accuracy_range_and_new_fields(self, client, white_game):
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        client.post("/game/move", json={"game_id": gid, "move_uci": "d2d4"})

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        data = r.json()
        # Nuova accuracy (curva logistica sul cp loss): sempre in [0, 100]
        assert 0.0 <= data["accuracy_score"] <= 100.0
        for m in data["moves"]:
            assert isinstance(m["is_mate_swing"], bool)
            assert "best_line_san" in m
            if m["classification"] in ("blunder", "mistake"):
                # Linea migliore popolata solo per errori gravi
                assert isinstance(m["best_line_san"], list)
                assert 1 <= len(m["best_line_san"]) <= 4
                assert all(isinstance(s, str) and s for s in m["best_line_san"])
            else:
                assert m["best_line_san"] is None
            # best_move ora deriva dalla PV di analyse (niente engine.play doppio)
            assert m["best_move_uci"] is None or isinstance(m["best_move_uci"], str)

    def test_analyze_mate_swing_clamped(self, client):
        # Partita iniettata deterministica: matto dell'imbecille
        # 1.f3 e5 2.g4 Qh4# — l'ultima mossa avviene in posizione con matto
        # forzato, quindi is_mate_swing deve essere true e loss_cp clampato.
        board = chess.Board()
        move_objects = []
        for uci in ["f2f3", "e7e5", "g2g4", "d8h4"]:
            mv = chess.Move.from_uci(uci)
            move_objects.append(mv)
            board.push(mv)
        games["matetest"] = {
            "board": board,
            "player_color": "white",
            "engine_elo": 800,
            "move_objects": move_objects,
            "last_engine_move": None,
            "created_at": "2026.07.08",
        }
        r = client.post("/game/analyze", json={"game_id": "matetest", "depth": 8})
        assert r.status_code == 200
        last = r.json()["moves"][-1]
        assert last["is_mate_swing"] is True
        assert -1000 <= last["loss_cp"] <= 1000


# -------------------------------------------------------------------
# /game/{id}/hint
# -------------------------------------------------------------------
class TestHint:
    def test_hint_default_multipv(self, client, white_game):
        gid = white_game["game_id"]
        r = client.post(f"/game/{gid}/hint", json={"depth": 8})
        assert r.status_code == 200
        data = r.json()
        assert len(data["lines"]) == 3  # multipv default
        # Ogni linea ha i campi attesi e la mossa è legale nella posizione corrente
        board = chess.Board(white_game["fen"])
        legal_ucis = {m.uci() for m in board.legal_moves}
        for line in data["lines"]:
            assert line["move_uci"] in legal_ucis
            assert line["move_san"] == board.san(chess.Move.from_uci(line["move_uci"]))
            assert isinstance(line["score_cp"], int)
        # lines[0] è la migliore per chi muove (bianco → score decrescente)
        scores = [line["score_cp"] for line in data["lines"]]
        assert scores == sorted(scores, reverse=True)
        assert data["eval_cp"] == data["lines"][0]["score_cp"]

    def test_hint_explicit_multipv(self, client, white_game):
        gid = white_game["game_id"]
        r = client.post(f"/game/{gid}/hint", json={"multipv": 1, "depth": 8})
        assert r.status_code == 200
        assert len(r.json()["lines"]) == 1

    def test_hint_multipv_out_of_range(self, client, white_game):
        gid = white_game["game_id"]
        r = client.post(f"/game/{gid}/hint", json={"multipv": 6, "depth": 8})
        assert r.status_code == 422
        r = client.post(f"/game/{gid}/hint", json={"multipv": 0, "depth": 8})
        assert r.status_code == 422

    def test_hint_game_not_found(self, client):
        r = client.post("/game/nonexist/hint", json={})
        assert r.status_code == 404

    def test_hint_game_over(self, client, white_game):
        gid = white_game["game_id"]
        # Forza un game-over deterministico: matto del barbiere sul board in-memory
        games[gid]["board"] = chess.Board(
            "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
        )
        r = client.post(f"/game/{gid}/hint", json={"depth": 8})
        assert r.status_code == 400

    def test_hint_does_not_alter_state(self, client, white_game):
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        before = client.get(f"/game/{gid}").json()

        r = client.post(f"/game/{gid}/hint", json={"depth": 8})
        assert r.status_code == 200

        after = client.get(f"/game/{gid}").json()
        assert after["fen"] == before["fen"]
        assert after["move_history"] == before["move_history"]
        assert after["turn"] == before["turn"]


# -------------------------------------------------------------------
# /health
# -------------------------------------------------------------------
class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# -------------------------------------------------------------------
# Persistenza (Fase 3): write-through cache + timing + start_fen
# -------------------------------------------------------------------
class TestPersistence:
    def test_new_game_creates_db_row(self, client):
        r = client.post("/game/new", json={"player_color": "white", "engine_elo": 800})
        gid = r.json()["game_id"]
        with SessionLocal() as db:
            row = db.get(Game, gid)
            assert row is not None
            assert row.player_color == "white"
            assert row.engine_elo == 800
            assert row.source == "play"
            assert row.start_fen is None
            assert row.created_at is not None
            assert row.pgn is not None
            # Nessuna mossa ancora (player bianco muove per primo)
            assert row.moves == []

    def test_new_game_black_persists_opening_move(self, client):
        """Player nero → Stockfish gioca il ply 1 (bianco), che va persistito."""
        r = client.post("/game/new", json={"player_color": "black", "engine_elo": 800})
        gid = r.json()["game_id"]
        with SessionLocal() as db:
            moves = (
                db.execute(select(Move).where(Move.game_id == gid).order_by(Move.ply))
                .scalars()
                .all()
            )
            assert len(moves) == 1
            assert moves[0].ply == 1
            assert moves[0].color == "white"
            assert moves[0].fen_before == chess.Board().fen()
            assert moves[0].think_ms is not None  # wall-time reale della ricerca

    def test_move_creates_db_rows(self, client, white_game):
        gid = white_game["game_id"]
        r = client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        assert r.status_code == 200
        with SessionLocal() as db:
            moves = (
                db.execute(select(Move).where(Move.game_id == gid).order_by(Move.ply))
                .scalars()
                .all()
            )
            # mossa player + risposta engine
            assert len(moves) == 2
            player_mv, engine_mv = moves
            assert player_mv.ply == 1
            assert player_mv.color == "white"
            assert player_mv.uci == "e2e4"
            assert player_mv.san == "e4"
            assert player_mv.fen_before == chess.Board().fen()
            assert engine_mv.ply == 2
            assert engine_mv.color == "black"
            # PGN snapshot aggiornato ad ogni persistenza
            row = db.get(Game, gid)
            assert row.pgn and "1." in row.pgn

    def test_think_ms_captured_on_move(self, client, white_game):
        """think_ms non-null sia sulla mossa player (marker last_ready_at) sia
        sulla risposta engine (wall-time reale)."""
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        with SessionLocal() as db:
            moves = (
                db.execute(select(Move).where(Move.game_id == gid).order_by(Move.ply))
                .scalars()
                .all()
            )
            player_mv, engine_mv = moves
            assert player_mv.think_ms is not None
            assert player_mv.think_ms >= 0
            assert engine_mv.think_ms is not None
            assert engine_mv.think_ms >= 0

    def test_cache_miss_recovery(self, client, white_game):
        """Simula un restart svuotando la cache in-memory: GET /game/{id} deve
        ricostruire la board dal DB rigiocando gli UCI."""
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        client.post("/game/move", json={"game_id": gid, "move_uci": "g1f3"})
        before = client.get(f"/game/{gid}").json()

        # "Restart": la cache viva sparisce, il DB resta.
        games.clear()
        assert gid not in games

        after = client.get(f"/game/{gid}").json()
        assert gid in games  # cache ripopolata dal DB
        assert after["fen"] == before["fen"]
        assert after["move_history"] == before["move_history"]
        assert after["move_history_san"] == before["move_history_san"]
        # La board ricostruita accetta ancora mosse legali coerenti.
        assert after["turn"] == before["turn"]

    def test_cache_miss_not_found(self, client):
        """Cache miss + riga DB assente → 404 (non 500)."""
        games.clear()
        r = client.get("/game/deadbeef")
        assert r.status_code == 404

    def test_start_fen_flows_through(self, client):
        custom = "4k3/8/4K3/8/8/8/8/7R w - - 0 1"
        r = client.post(
            "/game/new",
            json={"player_color": "white", "engine_elo": 800, "start_fen": custom},
        )
        assert r.status_code == 200
        data = r.json()
        # Player bianco + FEN con bianco al tratto: nessuna mossa engine → FEN invariata
        assert data["fen"] == custom
        assert data["move_history"] == []
        with SessionLocal() as db:
            row = db.get(Game, data["game_id"])
            assert row.start_fen == custom

    def test_start_fen_reconstructs_on_cache_miss(self, client):
        """Una partita con start_fen custom si ricostruisce dalla posizione
        giusta dopo un cache miss (non dalla posizione standard)."""
        custom = "4k3/8/4K3/8/8/8/8/7R w - - 0 1"
        r = client.post(
            "/game/new",
            json={"player_color": "white", "engine_elo": 800, "start_fen": custom},
        )
        gid = r.json()["game_id"]
        games.clear()
        after = client.get(f"/game/{gid}").json()
        assert after["fen"] == custom

    def test_invalid_start_fen_rejected(self, client):
        r = client.post(
            "/game/new",
            json={"player_color": "white", "engine_elo": 800, "start_fen": "not-a-fen"},
        )
        assert r.status_code == 400
