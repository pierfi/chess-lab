"""Test end-to-end per Chess Lab API."""

from datetime import datetime, timedelta

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import (
    SIM_ELO_K,
    SIM_ELO_SEED,
    _elo_expected,
    app,
    games,
)
from backend.db import AnalysisResult, Puzzle, SessionLocal, SrsCard, Game, Move, utcnow


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

    def test_hint_with_hint_elo(self, client, white_game):
        # hint_elo calibra la forza del suggerimento: stessa shape di risposta,
        # mosse comunque legali nella posizione corrente
        gid = white_game["game_id"]
        r = client.post(f"/game/{gid}/hint", json={"depth": 8, "hint_elo": 800})
        assert r.status_code == 200
        data = r.json()
        assert len(data["lines"]) == 3
        board = chess.Board(white_game["fen"])
        legal_ucis = {m.uci() for m in board.legal_moves}
        for line in data["lines"]:
            assert line["move_uci"] in legal_ucis
            assert isinstance(line["score_cp"], int)
        assert data["eval_cp"] == data["lines"][0]["score_cp"]

    def test_hint_elo_out_of_range(self, client, white_game):
        gid = white_game["game_id"]
        r = client.post(f"/game/{gid}/hint", json={"depth": 8, "hint_elo": 100})
        assert r.status_code == 422
        r = client.post(f"/game/{gid}/hint", json={"depth": 8, "hint_elo": 3000})
        assert r.status_code == 422

    def test_hint_elo_null_is_default(self, client, white_game):
        # hint_elo esplicitamente null = campo omesso = piena forza
        gid = white_game["game_id"]
        r = client.post(f"/game/{gid}/hint", json={"depth": 8, "hint_elo": None})
        assert r.status_code == 200
        assert len(r.json()["lines"]) == 3

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
# /game/{id}/threats — pezzi in presa (attaccati E indifesi), v1
# -------------------------------------------------------------------
class TestThreats:
    def _inject(self, game_id: str, fen: str, player_color: str = "white"):
        """Partita iniettata in sola cache con posizione deterministica
        (stesso pattern di test_analyze_mate_swing_clamped)."""
        games[game_id] = {
            "board": chess.Board(fen),
            "player_color": player_color,
            "engine_elo": 800,
            "move_objects": [],
            "last_engine_move": None,
            "created_at": "2026.07.17",
        }

    def test_hanging_piece_detected(self, client):
        # Cavallo bianco in d4 attaccato dal pedone e5 e non difeso da nessuno.
        self._inject("thr_hang", "k7/8/8/4p3/3N4/8/8/K7 w - - 0 1")
        r = client.get("/game/thr_hang/threats")
        assert r.status_code == 200
        data = r.json()
        assert data["side"] == "white"
        assert data["in_presa"] == [
            {"square": "d4", "piece": "N", "value": 3, "attackers": ["e5"]}
        ]

    def test_defended_piece_not_flagged(self, client):
        # Stessa posizione ma col pedone c3 che difende il cavallo d4:
        # attaccato MA difeso -> non in presa (definizione v1, niente SEE).
        self._inject("thr_def", "k7/8/8/4p3/3N4/2P5/8/K7 w - - 0 1")
        r = client.get("/game/thr_def/threats")
        assert r.status_code == 200
        assert r.json()["in_presa"] == []

    def test_own_color_only(self, client):
        # Cavallo NERO in d4 attaccato dal pedone bianco c3, indifeso — ma al
        # tratto c'è il bianco: si valutano solo i pezzi del lato al tratto.
        self._inject("thr_own", "k7/8/8/8/3n4/2P5/8/K7 w - - 0 1")
        r = client.get("/game/thr_own/threats")
        assert r.status_code == 200
        data = r.json()
        assert data["side"] == "white"
        assert data["in_presa"] == []

    def test_king_never_listed(self, client):
        # Re bianco h1 attaccato dalla torre a1 (scacco, non matto: Kh2 legale).
        # Il re non entra mai in in_presa: lo scacco è dominio di .king-check.
        self._inject("thr_king", "k7/8/8/8/8/8/8/r6K w - - 0 1")
        r = client.get("/game/thr_king/threats")
        assert r.status_code == 200
        assert r.json()["in_presa"] == []

    def test_multiple_attackers_sorted(self, client):
        # Torre bianca d5 attaccata da due pedoni neri (c6 ed e6), indifesa:
        # entrambe le caselle attaccanti riportate, ordinate.
        self._inject("thr_multi", "k7/8/2p1p3/3R4/8/8/8/K7 w - - 0 1")
        r = client.get("/game/thr_multi/threats")
        assert r.status_code == 200
        entries = r.json()["in_presa"]
        assert len(entries) == 1
        assert entries[0]["square"] == "d5"
        assert entries[0]["value"] == 5
        assert entries[0]["attackers"] == ["c6", "e6"]

    def test_black_to_move_side(self, client):
        # Al tratto c'è il nero: side="black" e pezzi neri (lettera FEN
        # minuscola). Cavallo nero d4 attaccato dal pedone bianco c3, indifeso.
        self._inject("thr_black", "k7/8/8/8/3n4/2P5/8/K7 b - - 0 1", player_color="black")
        r = client.get("/game/thr_black/threats")
        assert r.status_code == 200
        data = r.json()
        assert data["side"] == "black"
        assert data["in_presa"] == [
            {"square": "d4", "piece": "n", "value": 3, "attackers": ["c3"]}
        ]

    def test_threats_game_over(self, client, white_game):
        gid = white_game["game_id"]
        # Matto del barbiere: partita finita -> 400, stessa convenzione di /hint
        games[gid]["board"] = chess.Board(
            "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
        )
        r = client.get(f"/game/{gid}/threats")
        assert r.status_code == 400

    def test_threats_game_not_found(self, client):
        r = client.get("/game/nonexist/threats")
        assert r.status_code == 404

    def test_threats_do_not_alter_state(self, client, white_game):
        gid = white_game["game_id"]
        before = client.get(f"/game/{gid}").json()
        r = client.get(f"/game/{gid}/threats")
        assert r.status_code == 200
        after = client.get(f"/game/{gid}").json()
        assert after["fen"] == before["fen"]
        assert after["move_history"] == before["move_history"]


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


# -------------------------------------------------------------------
# /game/analyze — persistenza in analysis_results + riepilogo su games
# -------------------------------------------------------------------
class TestAnalyzePersistence:
    def test_analyze_persists_results_and_summary(self, client, white_game):
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        client.post("/game/move", json={"game_id": gid, "move_uci": "d2d4"})

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        data = r.json()

        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(AnalysisResult)
                    .where(AnalysisResult.game_id == gid)
                    .order_by(AnalysisResult.ply)
                )
                .scalars()
                .all()
            )
            assert len(rows) == data["total_moves"]
            for row, m in zip(rows, data["moves"]):
                assert row.ply == m["ply"]
                assert row.classification == m["classification"]
                assert row.loss_cp == m["loss_cp"]
                assert row.score_cp == m["score_cp"]
                assert row.best_move_uci == m["best_move_uci"]
                assert row.is_mate_swing == m["is_mate_swing"]

            game_row = db.get(Game, gid)
            assert game_row.analyzed_at is not None
            assert game_row.player_accuracy == data["accuracy_score"]
            assert game_row.blunders == data["blunders"]
            assert game_row.mistakes == data["mistakes"]
            assert game_row.inaccuracies == data["inaccuracies"]

    def test_analyze_is_idempotent_no_duplicate_rows(self, client, white_game):
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})

        client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        client.post("/game/analyze", json={"game_id": gid, "depth": 8})  # re-analisi

        with SessionLocal() as db:
            rows = (
                db.execute(select(AnalysisResult).where(AnalysisResult.game_id == gid))
                .scalars()
                .all()
            )
            plies = [row.ply for row in rows]
            assert len(plies) == len(set(plies))  # nessun duplicato per ply

    def test_analyze_without_db_row_does_not_500(self, client):
        """Una partita iniettata solo in cache (nessuna riga games, come nel
        test preesistente test_analyze_mate_swing_clamped) deve restare
        analizzabile senza persistenza — _persist_analysis deve fare no-op,
        non fallire la FK su analysis_results."""
        board = chess.Board()
        move_objects = []
        for uci in ["f2f3", "e7e5", "g2g4", "d8h4"]:
            mv = chess.Move.from_uci(uci)
            move_objects.append(mv)
            board.push(mv)
        games["nodbrow1"] = {
            "board": board,
            "player_color": "white",
            "engine_elo": 800,
            "move_objects": move_objects,
            "last_engine_move": None,
            "created_at": "2026.07.11",
        }
        r = client.post("/game/analyze", json={"game_id": "nodbrow1", "depth": 8})
        assert r.status_code == 200
        with SessionLocal() as db:
            assert db.get(Game, "nodbrow1") is None
            rows = (
                db.execute(select(AnalysisResult).where(AnalysisResult.game_id == "nodbrow1"))
                .scalars()
                .all()
            )
            assert rows == []


# -------------------------------------------------------------------
# GET /games — lista paginata/filtrata
# -------------------------------------------------------------------
class TestGamesList:
    # Nota: l'intera classe di test condivide UN SOLO DB temporaneo per l'intera
    # sessione pytest (vedi conftest.py) — non viene ripulito tra i test file.
    # Molti altri test creano partite reali con source='play' (fixture
    # white_game/black_game), quindi i controlli qui sotto usano containment
    # (subset) invece di uguaglianza stretta dove il filtro si sovrappone a
    # dati creati altrove. Solo i filtri per `result` (win/loss/draw) sono al
    # sicuro da uguaglianza stretta: nessun altro test scrive un result reale
    # nel DB (le partite create dai fixture non arrivano mai a game-over).
    # Fixture scope="class" (non function): i game_id sono fissi, un secondo
    # insert delle stesse righe per-test violerebbe la UNIQUE constraint.
    @pytest.fixture(scope="class", autouse=True)
    def seed(self):
        rows = [
            ("wingame1", "white", "1-0", "play"),      # win per player bianco
            ("winbygam", "black", "0-1", "play"),      # win per player nero
            ("lossgam1", "white", "0-1", "play"),       # loss per player bianco
            ("drawgame", "black", "1/2-1/2", "play"),  # draw
            ("importga", "white", "1-0", "import"),     # esclusa di default
        ]
        with SessionLocal() as db:
            for gid, color, result, source in rows:
                db.add(Game(
                    id=gid, player_color=color, engine_elo=800,
                    result=result, source=source, created_at=utcnow(),
                ))
            db.commit()
        yield

    def test_default_source_excludes_import(self, client):
        r = client.get("/games", params={"per_page": 100})
        assert r.status_code == 200
        data = r.json()
        ids = {item["game_id"] for item in data["items"]}
        assert "importga" not in ids
        assert {"wingame1", "winbygam", "lossgam1", "drawgame"} <= ids

    def test_filter_color(self, client):
        r = client.get("/games", params={"color": "black", "per_page": 100})
        data = r.json()
        assert all(item["player_color"] == "black" for item in data["items"])
        ids = {item["game_id"] for item in data["items"]}
        assert {"winbygam", "drawgame"} <= ids

    def test_filter_result_win_relative_to_player_color(self, client):
        r = client.get("/games", params={"result": "win", "per_page": 100})
        data = r.json()
        ids = {item["game_id"] for item in data["items"]}
        assert ids == {"wingame1", "winbygam"}

    def test_filter_result_loss(self, client):
        r = client.get("/games", params={"result": "loss", "per_page": 100})
        data = r.json()
        ids = {item["game_id"] for item in data["items"]}
        assert ids == {"lossgam1"}

    def test_filter_result_draw(self, client):
        r = client.get("/games", params={"result": "draw", "per_page": 100})
        data = r.json()
        ids = {item["game_id"] for item in data["items"]}
        assert ids == {"drawgame"}

    def test_filter_source_import(self, client):
        r = client.get("/games", params={"source": "import", "per_page": 100})
        data = r.json()
        ids = {item["game_id"] for item in data["items"]}
        assert ids == {"importga"}

    def test_pagination_mechanics(self, client):
        # Non assume un totale fisso (il DB condiviso con l'intera sessione di
        # test contiene molte altre partite 'play'): verifica solo il
        # contratto di paginazione — dimensione pagina rispettata, pagine
        # diverse restituiscono partite diverse, stesso totale su entrambe.
        r1 = client.get("/games", params={"per_page": 1, "page": 1})
        d1 = r1.json()
        assert d1["per_page"] == 1
        assert d1["page"] == 1
        assert len(d1["items"]) == 1
        assert d1["total"] >= 4  # almeno le 4 partite 'play' seedate qui

        r2 = client.get("/games", params={"per_page": 1, "page": 2})
        d2 = r2.json()
        assert d2["page"] == 2
        assert d2["total"] == d1["total"]
        assert d2["items"][0]["game_id"] != d1["items"][0]["game_id"]

    def test_invalid_result_filter_rejected(self, client):
        r = client.get("/games", params={"result": "bogus"})
        assert r.status_code == 422

    def test_move_count_and_analysis_fields(self, client, white_game):
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})

        r = client.get("/games", params={"per_page": 100})
        item = next(i for i in r.json()["items"] if i["game_id"] == gid)
        assert item["move_count"] == 2
        assert item["analyzed_at"] is None
        assert item["player_accuracy"] is None
        assert item["blunders"] is None

        client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        r = client.get("/games", params={"per_page": 100})
        item = next(i for i in r.json()["items"] if i["game_id"] == gid)
        assert item["analyzed_at"] is not None
        assert item["player_accuracy"] is not None
        assert item["blunders"] is not None


# -------------------------------------------------------------------
# GET /game/{id}/replay
# -------------------------------------------------------------------
class TestReplay:
    def test_replay_shape(self, client, white_game):
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        client.post("/game/move", json={"game_id": gid, "move_uci": "g1f3"})

        r = client.get(f"/game/{gid}/replay")
        assert r.status_code == 200
        data = r.json()
        state = client.get(f"/game/{gid}").json()

        assert len(data["fens"]) == len(data["moves"]) + 1
        assert data["fens"][0] == chess.Board().fen()
        assert data["fens"][-1] == state["fen"]
        assert data["pgn"] == state["pgn"]
        for m, uci in zip(data["moves"], state["move_history"]):
            assert m["uci"] == uci
        assert data["moves"][0]["san"] == "e4"

    def test_replay_not_found(self, client):
        r = client.get("/game/nonexist/replay")
        assert r.status_code == 404

    def test_replay_survives_cache_miss(self, client, white_game):
        gid = white_game["game_id"]
        # Una sola /game/move produce 2 ply (mossa player + risposta engine).
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        games.clear()
        r = client.get(f"/game/{gid}/replay")
        assert r.status_code == 200
        assert len(r.json()["fens"]) == 3  # 2 ply + posizione finale


# -------------------------------------------------------------------
# DELETE /game/{id}
# -------------------------------------------------------------------
class TestDeleteGame:
    def test_delete_cascades_and_evicts_cache(self, client, white_game):
        gid = white_game["game_id"]
        client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert gid in games

        with SessionLocal() as db:
            assert db.execute(
                select(Move).where(Move.game_id == gid)
            ).scalars().all()
            assert db.execute(
                select(AnalysisResult).where(AnalysisResult.game_id == gid)
            ).scalars().all()

        r = client.delete(f"/game/{gid}")
        assert r.status_code == 200
        assert r.json() == {"deleted": True, "game_id": gid}
        assert gid not in games  # evicted dalla cache, non resuscitabile

        with SessionLocal() as db:
            assert db.get(Game, gid) is None
            # Cascade DB (ON DELETE CASCADE + foreign_keys=ON): verificato in
            # pratica, non assunto.
            assert db.execute(select(Move).where(Move.game_id == gid)).scalars().all() == []
            assert db.execute(
                select(AnalysisResult).where(AnalysisResult.game_id == gid)
            ).scalars().all() == []

        r = client.get(f"/game/{gid}")
        assert r.status_code == 404

    def test_delete_not_found(self, client):
        r = client.delete("/game/nonexist")
        assert r.status_code == 404


# -------------------------------------------------------------------
# POST /games/import
# -------------------------------------------------------------------
SAMPLE_PGN = """[Event "Test"]
[Site "?"]
[Date "2026.07.11"]
[Round "1"]
[White "A"]
[Black "B"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O 1-0
"""

# PGN con header FEN custom (Nero al tratto — Bug #8/#9): stessa posizione di
# Philidor usata dal drill di finali. Copertura del gap esplicitamente
# segnalato in docs/bugs.md: nessun test incrociava start_fen (via import) con
# /game/analyze prima di questo fix.
FEN_PGN = """[Event "Test"]
[Site "?"]
[Date "2026.07.17"]
[Round "1"]
[White "A"]
[Black "B"]
[Result "*"]
[FEN "8/8/8/3k4/8/3K4/3P4/3r4 b - - 0 1"]
[SetUp "1"]

1...Rxd2 2.Kxd2 *
"""


class TestImportPgn:
    def test_import_creates_game_and_moves(self, client):
        r = client.post("/games/import", json={"pgn": SAMPLE_PGN})
        assert r.status_code == 200
        data = r.json()
        gid = data["game_id"]
        assert data["source"] == "import"
        assert len(data["move_history"]) == 9  # 9 mezze mosse nella mainline

        with SessionLocal() as db:
            row = db.get(Game, gid)
            assert row is not None
            assert row.source == "import"
            assert row.player_color == "white"
            moves = (
                db.execute(select(Move).where(Move.game_id == gid).order_by(Move.ply))
                .scalars()
                .all()
            )
            assert len(moves) == 9
            assert moves[0].uci == "e2e4"
            assert moves[0].san == "e4"
            assert all(m.think_ms is None for m in moves)

    def test_import_game_is_playable_and_analyzable(self, client):
        r = client.post("/games/import", json={"pgn": SAMPLE_PGN})
        gid = r.json()["game_id"]

        r = client.get(f"/game/{gid}")
        assert r.status_code == 200

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        assert r.json()["total_moves"] == 9

    def test_import_no_moves_rejected(self, client):
        # Testo non-PGN: chess.pgn.read_game() lo tollera restituendo un Game
        # valido a zero mosse — è così, non con parsed.errors, che rileviamo
        # l'input spazzatura.
        r = client.post("/games/import", json={"pgn": "this is not a pgn at all !!! ###"})
        assert r.status_code == 400

    def test_import_empty_string_rejected(self, client):
        r = client.post("/games/import", json={"pgn": ""})
        assert r.status_code == 400

    def test_import_with_fen_header_is_analyzable(self, client):
        """Bug #8/#9 regression: un import con header FEN (Nero al tratto)
        deve restare analizzabile — prima del fix, analyze_game() ignorava
        start_fen e la chiamata restava appesa a tempo indefinito."""
        r = client.post("/games/import", json={"pgn": FEN_PGN})
        assert r.status_code == 200
        data = r.json()
        gid = data["game_id"]
        assert data["source"] == "import"
        assert len(data["move_history"]) == 2

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        analysis = r.json()
        assert analysis["total_moves"] == 2

        # Ply 1 è del Nero (start_fen col Nero al tratto): move_number deve
        # riflettere questo, non assumere Bianco-first (Bug #9).
        ply1, ply2 = analysis["moves"]
        assert ply1["color"] == "black"
        assert ply1["move_uci"] == "d1d2"
        assert ply1["move_number"] == 1
        assert ply2["color"] == "white"
        assert ply2["move_uci"] == "d3d2"
        assert ply2["move_number"] == 2


# -------------------------------------------------------------------
# GET /stats/summary — numeri headline aggregati
# -------------------------------------------------------------------
class TestStatsSummary:
    # Il DB temporaneo è condiviso da tutta la sessione pytest (vedi conftest.py),
    # e /stats aggrega su TUTTO lo storico. Per asserzioni deterministiche i test
    # isolano i propri dati con `source` custom (gli altri test scrivono solo
    # 'play'/'import'), poi filtrano /stats?source=<custom>.
    @pytest.fixture(scope="class", autouse=True)
    def seed(self):
        base = datetime(2026, 3, 1, 12, 0, 0)
        with SessionLocal() as db:
            # 2 analizzate (accuracy nota), 1 win nera non analizzata, 1 patta,
            # 1 in corso (result None) → total 5, decise 4.
            db.add(Game(id="ss_win_a", player_color="white", engine_elo=1000,
                        result="1-0", source="statssum", created_at=base,
                        analyzed_at=utcnow(), player_accuracy=80.0,
                        blunders=1, mistakes=2, inaccuracies=3))
            db.add(Game(id="ss_loss_", player_color="white", engine_elo=1000,
                        result="0-1", source="statssum",
                        created_at=base + timedelta(minutes=1),
                        analyzed_at=utcnow(), player_accuracy=60.0,
                        blunders=3, mistakes=1, inaccuracies=0))
            db.add(Game(id="ss_bwin_", player_color="black", engine_elo=1000,
                        result="0-1", source="statssum",
                        created_at=base + timedelta(minutes=2)))
            db.add(Game(id="ss_draw_", player_color="white", engine_elo=1000,
                        result="1/2-1/2", source="statssum",
                        created_at=base + timedelta(minutes=3)))
            db.add(Game(id="ss_prog_", player_color="white", engine_elo=1000,
                        result=None, source="statssum",
                        created_at=base + timedelta(minutes=4)))
            # Mosse su ss_win_a (player bianco): due mosse bianche (player) +
            # una nera (engine, esclusa dalla media think_ms).
            for ply, color, uci, san, tm in [
                (1, "white", "e2e4", "e4", 1000),
                (2, "black", "e7e5", "e5", 3000),
                (3, "white", "g1f3", "Nf3", 2000),
            ]:
                db.add(Move(game_id="ss_win_a", ply=ply, color=color, uci=uci,
                            san=san, fen_before="startfen", think_ms=tm,
                            created_at=base))
            db.commit()
        yield

    def test_summary_counts_and_rates(self, client):
        d = client.get("/stats/summary", params={"source": "statssum"}).json()
        assert d["total_games"] == 5
        assert d["decided_games"] == 4  # esclude la partita in corso
        assert d["wins"] == 2  # ss_win_a (bianco 1-0) + ss_bwin_ (nero 0-1)
        assert d["losses"] == 1
        assert d["draws"] == 1
        assert d["win_rate"] == 0.5
        assert d["loss_rate"] == 0.25
        assert d["draw_rate"] == 0.25

    def test_summary_accuracy_only_over_analyzed(self, client):
        d = client.get("/stats/summary", params={"source": "statssum"}).json()
        assert d["analyzed_games"] == 2
        # Media SOLO sulle 2 analizzate: (80+60)/2. Le non analizzate non contano
        # come 0, sono escluse dal denominatore.
        assert d["avg_accuracy"] == 70.0
        assert d["total_blunders"] == 4
        assert d["total_mistakes"] == 3
        assert d["total_inaccuracies"] == 3

    def test_summary_think_ms_player_moves_only(self, client):
        d = client.get("/stats/summary", params={"source": "statssum"}).json()
        # Solo mosse del player (bianche): (1000+2000)/2; la mossa engine (3000)
        # è esclusa.
        assert d["avg_think_ms_per_move"] == 1500

    def test_summary_color_filter(self, client):
        d = client.get(
            "/stats/summary", params={"source": "statssum", "color": "black"}
        ).json()
        assert d["total_games"] == 1
        assert d["decided_games"] == 1
        assert d["wins"] == 1  # nero con 0-1 = vittoria del player
        assert d["avg_accuracy"] is None  # la partita nera non è analizzata

    def test_summary_empty_history(self, client):
        d = client.get("/stats/summary", params={"source": "statsempty"}).json()
        assert d["total_games"] == 0
        assert d["decided_games"] == 0
        assert d["wins"] == 0
        assert d["win_rate"] == 0.0
        assert d["loss_rate"] == 0.0
        assert d["draw_rate"] == 0.0
        assert d["avg_accuracy"] is None
        assert d["avg_think_ms_per_move"] is None
        assert d["total_blunders"] == 0

    def test_summary_date_range(self, client):
        # date_to è inclusivo del giorno intero: 2026-03-01 copre tutte le 5.
        d = client.get("/stats/summary", params={
            "source": "statssum", "date_from": "2026-03-01", "date_to": "2026-03-01",
        }).json()
        assert d["total_games"] == 5
        # Un intervallo che finisce prima: nessuna partita.
        d2 = client.get("/stats/summary", params={
            "source": "statssum", "date_from": "2026-02-01", "date_to": "2026-02-28",
        }).json()
        assert d2["total_games"] == 0

    def test_summary_invalid_date(self, client):
        r = client.get(
            "/stats/summary", params={"source": "statssum", "date_from": "not-a-date"}
        )
        assert r.status_code == 400


# -------------------------------------------------------------------
# GET /stats/progress — serie temporale + ELO simulato
# -------------------------------------------------------------------
class TestStatsProgress:
    @pytest.fixture(scope="class", autouse=True)
    def seed(self):
        base = datetime(2026, 4, 1, 12, 0, 0)
        with SessionLocal() as db:
            for i in range(3):  # 3 vittorie contro engine 1200
                db.add(Game(id=f"pw_{i}", player_color="white", engine_elo=1200,
                            result="1-0", source="statswin",
                            created_at=base + timedelta(minutes=i)))
            for i in range(3):  # 3 sconfitte
                db.add(Game(id=f"pl_{i}", player_color="white", engine_elo=1200,
                            result="0-1", source="statsloss",
                            created_at=base + timedelta(minutes=i)))
            db.commit()
        yield

    def test_progress_all_wins_increases(self, client):
        d = client.get("/stats/progress", params={"source": "statswin"}).json()
        assert d["games_counted"] == 3
        assert d["seed_elo"] == SIM_ELO_SEED
        assert d["k_factor"] == SIM_ELO_K
        elos = [p["simulated_elo"] for p in d["series"]]
        assert elos == sorted(elos)  # monotòna non decrescente
        assert d["series"][0]["simulated_elo"] > SIM_ELO_SEED
        assert d["current_elo"] == elos[-1]
        assert d["peak_elo"] == max(elos)
        assert all(p["result"] == "win" and p["score"] == 1.0 for p in d["series"])
        # game_number 1-based e progressivo
        assert [p["game_number"] for p in d["series"]] == [1, 2, 3]

    def test_progress_all_losses_decreases(self, client):
        d = client.get("/stats/progress", params={"source": "statsloss"}).json()
        assert d["games_counted"] == 3
        assert d["current_elo"] < SIM_ELO_SEED
        assert all(p["result"] == "loss" and p["score"] == 0.0 for p in d["series"])

    def test_progress_matches_elo_formula(self, client):
        # Sequenza deterministica win/loss/draw contro engine 1000: ricalcolo la
        # serie con la stessa formula e confronto punto per punto.
        base = datetime(2026, 5, 1, 12, 0, 0)
        with SessionLocal() as db:
            for i, res in enumerate(["1-0", "0-1", "1/2-1/2"]):
                db.add(Game(id=f"pm_{i}", player_color="white", engine_elo=1000,
                            result=res, source="statsmath",
                            created_at=base + timedelta(minutes=i)))
            db.commit()

        d = client.get("/stats/progress", params={"source": "statsmath"}).json()

        rating = float(SIM_ELO_SEED)
        expected = []
        for score in (1.0, 0.0, 0.5):
            rating += SIM_ELO_K * (score - _elo_expected(rating, 1000))
            expected.append(round(rating))
        assert [p["simulated_elo"] for p in d["series"]] == expected
        assert d["current_elo"] == expected[-1]
        assert d["peak_elo"] == max(expected + [SIM_ELO_SEED])

    def test_progress_skips_in_progress_games(self, client):
        base = datetime(2026, 6, 1, 12, 0, 0)
        with SessionLocal() as db:
            db.add(Game(id="pip_win", player_color="white", engine_elo=1200,
                        result="1-0", source="statsip", created_at=base))
            db.add(Game(id="pip_none", player_color="white", engine_elo=1200,
                        result=None, source="statsip",
                        created_at=base + timedelta(minutes=1)))
            db.commit()
        d = client.get("/stats/progress", params={"source": "statsip"}).json()
        assert d["games_counted"] == 1  # la partita in corso è saltata
        assert len(d["series"]) == 1

    def test_progress_empty_history(self, client):
        d = client.get("/stats/progress", params={"source": "statsnone"}).json()
        assert d["series"] == []
        assert d["games_counted"] == 0
        assert d["current_elo"] == SIM_ELO_SEED
        assert d["peak_elo"] == SIM_ELO_SEED
        assert d["recent"]["games"] == 0
        assert d["recent"]["elo_change"] == 0
        assert d["recent"]["avg_accuracy"] is None

    def test_progress_recent_accuracy_only_analyzed(self, client):
        base = datetime(2026, 7, 1, 12, 0, 0)
        with SessionLocal() as db:
            db.add(Game(id="pr_an", player_color="white", engine_elo=1200,
                        result="1-0", source="statsrec", created_at=base,
                        analyzed_at=utcnow(), player_accuracy=90.0))
            db.add(Game(id="pr_non", player_color="white", engine_elo=1200,
                        result="1-0", source="statsrec",
                        created_at=base + timedelta(minutes=1)))
            db.commit()
        d = client.get("/stats/progress", params={"source": "statsrec"}).json()
        assert d["recent"]["games"] == 2
        assert d["recent"]["wins"] == 2
        # Media accuracy recente solo sulla partita analizzata.
        assert d["recent"]["avg_accuracy"] == 90.0
        accs = [p["accuracy"] for p in d["series"]]
        assert 90.0 in accs and None in accs

    def test_progress_invalid_date(self, client):
        r = client.get(
            "/stats/progress", params={"source": "statswin", "date_from": "2026/01/01"}
        )
        assert r.status_code == 400


# -------------------------------------------------------------------
# Fase 4 — Allenamento mirato (docs/training-mode.md)
# -------------------------------------------------------------------
# Le date di creazione delle partite seminate qui usano anni futuri (2031+)
# apposta: GET /training/puzzles/next non filtra la coda di ripasso per
# source e ordina i candidati per games.created_at DESC, quindi una data
# lontana nel futuro garantisce che la riga seminata da un test vinca
# sull'eventuale storico organico prodotto da altri test in questo stesso
# processo (DB temporaneo condiviso per l'intera sessione, vedi conftest.py).
# La generazione di NUOVI puzzle è comunque isolata anche per source tramite
# il parametro opzionale ?source= (stesso pattern di GET /games e /stats/*).

def _seed_puzzle_candidate(
    db,
    game_id: str,
    source: str,
    created_at: datetime,
    classification: str = "blunder",
    player_color: str = "white",
    ply: int = 3,
    played_uci: str = "d1h5",
    best_move_uci: str = "g1f3",
    fen_before: str = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
):
    """Semina una partita minimale con una riga moves + analysis_results a un
    ply dato, pronta per essere pescata da /training/puzzles/next."""
    db.add(Game(id=game_id, player_color=player_color, engine_elo=800,
                source=source, created_at=created_at))
    db.flush()
    db.add(Move(game_id=game_id, ply=ply, color=player_color, uci=played_uci,
                san="Qh5", fen_before=fen_before, created_at=created_at))
    db.add(AnalysisResult(game_id=game_id, ply=ply, classification=classification,
                           loss_cp=250, score_cp=-100, best_move_uci=best_move_uci))


class TestTrainingPuzzlesNext:
    def test_generates_puzzle_from_blunder(self, client):
        src = "trainpz1"
        with SessionLocal() as db:
            _seed_puzzle_candidate(db, "tpz1a", src, datetime(2031, 1, 1))
            db.commit()

        r = client.get("/training/puzzles/next", params={"source": src})
        assert r.status_code == 200
        data = r.json()
        assert data["puzzle_id"] is not None
        assert data["game_id"] == "tpz1a"
        assert data["ply"] == 3
        assert data["source"] == "blunder"
        assert data["is_review"] is False
        assert data["due_at"] is None
        assert data["player_to_move"] == "white"  # FEN seminata: bianco al tratto

        with SessionLocal() as db:
            row = db.get(Puzzle, data["puzzle_id"])
            assert row is not None
            assert row.game_id == "tpz1a"
            assert row.ply == 3
            assert row.best_move_uci == "g1f3"
            assert row.source == "blunder"

    def test_picks_most_recent_candidate(self, client):
        src = "trainpz2"
        with SessionLocal() as db:
            _seed_puzzle_candidate(db, "tpz2a", src, datetime(2031, 2, 1))
            _seed_puzzle_candidate(db, "tpz2b", src, datetime(2031, 3, 1))  # più recente
            db.commit()

        data = client.get("/training/puzzles/next", params={"source": src}).json()
        assert data["game_id"] == "tpz2b"

    def test_already_puzzled_blunder_not_regenerated(self, client):
        src = "trainpz3"
        with SessionLocal() as db:
            _seed_puzzle_candidate(db, "tpz3a", src, datetime(2031, 4, 1))
            db.commit()

        first = client.get("/training/puzzles/next", params={"source": src}).json()
        assert first["puzzle_id"] is not None

        second = client.get("/training/puzzles/next", params={"source": src}).json()
        assert second["puzzle_id"] is None  # nessun'altra carta dovuta/candidata

    def test_fallback_to_inaccuracy(self, client):
        src = "trainpz4"
        with SessionLocal() as db:
            _seed_puzzle_candidate(
                db, "tpz4a", src, datetime(2031, 5, 1), classification="inaccuracy"
            )
            db.commit()

        data = client.get("/training/puzzles/next", params={"source": src}).json()
        assert data["puzzle_id"] is not None
        assert data["source"] == "inaccuracy"

    def test_blunder_preferred_over_inaccuracy(self, client):
        src = "trainpz5"
        with SessionLocal() as db:
            # l'inaccuracy è più recente ma il blunder/mistake ha priorità
            _seed_puzzle_candidate(
                db, "tpz5a", src, datetime(2031, 6, 1), classification="inaccuracy",
            )
            _seed_puzzle_candidate(
                db, "tpz5b", src, datetime(2031, 5, 15), classification="blunder",
            )
            db.commit()

        data = client.get("/training/puzzles/next", params={"source": src}).json()
        assert data["game_id"] == "tpz5b"
        assert data["source"] == "blunder"

    def test_ignores_engine_side_errors(self, client):
        """Un blunder/mistake della mossa dell'ENGINE (color diverso da
        player_color) non deve diventare un puzzle self-generated."""
        src = "trainpz6"
        with SessionLocal() as db:
            _seed_puzzle_candidate(
                db, "tpz6a", src, datetime(2031, 7, 1), player_color="white",
            )
            db.flush()
            mv = db.execute(select(Move).where(Move.game_id == "tpz6a")).scalar_one()
            mv.color = "black"  # simula un "errore" lato engine, non del player
            db.commit()

        data = client.get("/training/puzzles/next", params={"source": src}).json()
        assert data["puzzle_id"] is None

    def test_no_candidates_returns_none(self, client):
        data = client.get(
            "/training/puzzles/next", params={"source": "trainpz_empty"}
        ).json()
        assert data["puzzle_id"] is None
        assert "message" in data


class TestTrainingPuzzlesAnswer:
    def _generate_puzzle(self, client, src, game_id, seed_date):
        with SessionLocal() as db:
            _seed_puzzle_candidate(db, game_id, src, seed_date)
            db.commit()
        data = client.get("/training/puzzles/next", params={"source": src}).json()
        assert data["puzzle_id"] is not None
        return data["puzzle_id"]

    def test_wrong_answer(self, client):
        pid = self._generate_puzzle(client, "trainans1", "tans1a", datetime(2031, 8, 1))

        r = client.post(f"/training/puzzles/{pid}/answer", json={"move_uci": "a2a3"})
        assert r.status_code == 200
        data = r.json()
        assert data["correct"] is False
        assert data["best_move_uci"] == "g1f3"
        assert data["correct_streak"] == 0
        assert data["interval_days"] == 1
        assert abs(data["ease_factor"] - 2.3) < 1e-9  # 2.5 - 0.2

        with SessionLocal() as db:
            card = db.execute(
                select(SrsCard).where(SrsCard.puzzle_id == pid)
            ).scalar_one()
            assert card.correct_streak == 0
            assert card.interval_days == 1
            assert abs(card.ease_factor - 2.3) < 1e-9
            assert card.last_reviewed_at is not None
            assert card.due_at is not None

    def test_correct_answer_creates_card_on_first_attempt(self, client):
        """La carta SRS nasce al primo TENTATIVO, non alla generazione del
        puzzle (docs/training-mode.md) — non deve esistere prima di answer."""
        pid = self._generate_puzzle(client, "trainans2", "tans2a", datetime(2031, 8, 2))
        with SessionLocal() as db:
            assert db.execute(
                select(SrsCard).where(SrsCard.puzzle_id == pid)
            ).scalar_one_or_none() is None

        client.post(f"/training/puzzles/{pid}/answer", json={"move_uci": "g1f3"})
        with SessionLocal() as db:
            assert db.execute(
                select(SrsCard).where(SrsCard.puzzle_id == pid)
            ).scalar_one_or_none() is not None

    def test_sm2_progression_on_consecutive_correct_answers(self, client):
        """Progressione esatta dell'algoritmo SM-2 semplificato (spec
        docs/training-mode.md) su 4 risposte corrette consecutive da una
        carta nuova (ease_factor default 2.5):
        streak 1 -> interval 1,  poi ease 2.6
        streak 2 -> interval 3,  poi ease 2.7
        streak 3 -> interval round(3*2.7)=8,  poi ease 2.8
        streak 4 -> interval round(8*2.8)=22, poi ease 2.9
        """
        pid = self._generate_puzzle(client, "trainans3", "tans3a", datetime(2031, 8, 3))

        expected = [
            (1, 1, 2.6),
            (2, 3, 2.7),
            (3, 8, 2.8),
            (4, 22, 2.9),
        ]
        for streak, interval, ease in expected:
            r = client.post(f"/training/puzzles/{pid}/answer", json={"move_uci": "g1f3"})
            data = r.json()
            assert data["correct"] is True
            assert data["correct_streak"] == streak
            assert data["interval_days"] == interval
            assert abs(data["ease_factor"] - ease) < 1e-9

    def test_wrong_answer_resets_streak(self, client):
        pid = self._generate_puzzle(client, "trainans4", "tans4a", datetime(2031, 8, 4))
        client.post(f"/training/puzzles/{pid}/answer", json={"move_uci": "g1f3"})
        client.post(f"/training/puzzles/{pid}/answer", json={"move_uci": "g1f3"})
        # streak ora a 2 (interval 3, ease 2.7); una risposta sbagliata resetta tutto
        r = client.post(f"/training/puzzles/{pid}/answer", json={"move_uci": "a2a3"})
        data = r.json()
        assert data["correct"] is False
        assert data["correct_streak"] == 0
        assert data["interval_days"] == 1
        assert abs(data["ease_factor"] - 2.5) < 1e-9  # 2.7 - 0.2

    def test_answer_case_insensitive_uci_match(self, client):
        pid = self._generate_puzzle(client, "trainans5", "tans5a", datetime(2031, 8, 5))
        r = client.post(f"/training/puzzles/{pid}/answer", json={"move_uci": "G1F3"})
        assert r.json()["correct"] is True

    def test_answer_unknown_puzzle_404(self, client):
        r = client.post("/training/puzzles/999999/answer", json={"move_uci": "e2e4"})
        assert r.status_code == 404


# -------------------------------------------------------------------
# GET /training/weaknesses — fase di gioco + tema tattico probabile
# -------------------------------------------------------------------

def _seed_analysis_row(
    db,
    game_id: str,
    source: str,
    ply: int,
    player_color: str,
    fen_before: str,
    played_uci: str,
    best_move_uci: str,
    classification: str,
    loss_cp: int,
    score_cp: int,
):
    db.add(Game(id=game_id, player_color=player_color, engine_elo=800,
                source=source, created_at=datetime(2026, 1, 1)))
    db.flush()
    db.add(Move(game_id=game_id, ply=ply, color=player_color, uci=played_uci,
                san="-", fen_before=fen_before, created_at=datetime(2026, 1, 1)))
    db.add(AnalysisResult(game_id=game_id, ply=ply, classification=classification,
                           loss_cp=loss_cp, score_cp=score_cp, best_move_uci=best_move_uci))


class TestTrainingWeaknesses:
    def test_phase_aggregation(self, client):
        src = "trainweak_phase"
        with SessionLocal() as db:
            # apertura: ply <= 20 (indipendentemente dal materiale sul FEN)
            _seed_analysis_row(
                db, "twp1", src, ply=5, player_color="white",
                fen_before=chess.Board().fen(), played_uci="e2e4",
                best_move_uci="e2e4", classification="good", loss_cp=0, score_cp=10,
            )
            # mediogioco: ply > 20, materiale residuo abbondante
            middlegame_fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1"
            _seed_analysis_row(
                db, "twp2", src, ply=25, player_color="white",
                fen_before=middlegame_fen, played_uci="f1c4",
                best_move_uci="f1b5", classification="mistake", loss_cp=100, score_cp=-50,
            )
            # finale: ply > 20, materiale residuo sotto soglia
            endgame_fen = "8/8/4k3/8/4P3/4K3/8/8 w - - 0 40"
            _seed_analysis_row(
                db, "twp3", src, ply=41, player_color="white",
                fen_before=endgame_fen, played_uci="e3d4",
                best_move_uci="e3f4", classification="blunder", loss_cp=300, score_cp=-200,
            )
            db.commit()

        r = client.get("/training/weaknesses", params={"source": src})
        assert r.status_code == 200
        data = r.json()
        assert data["by_phase"]["opening"] == {"avg_loss_cp": 0.0, "count": 1}
        assert data["by_phase"]["middlegame"] == {"avg_loss_cp": 100.0, "count": 1}
        assert data["by_phase"]["endgame"] == {"avg_loss_cp": 300.0, "count": 1}
        assert "note" in data

    def test_empty_history(self, client):
        r = client.get("/training/weaknesses", params={"source": "trainweak_empty"})
        data = r.json()
        for phase in ("opening", "middlegame", "endgame"):
            assert data["by_phase"][phase] == {"avg_loss_cp": None, "count": 0}
        for theme in ("fork", "pin", "king_safety"):
            assert data["by_theme"][theme] == {"missed_count": 0}

    def test_theme_fork_missed(self, client):
        src = "trainweak_fork"
        fen = "r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1"
        with SessionLocal() as db:
            _seed_analysis_row(
                db, "twf1", src, ply=30, player_color="white",
                fen_before=fen, played_uci="b5a3",  # ritirata, non forchetta
                best_move_uci="b5c7",  # Nc7+ forchetta re+torre
                classification="blunder", loss_cp=300, score_cp=-200,
            )
            db.commit()

        data = client.get("/training/weaknesses", params={"source": src}).json()
        assert data["by_theme"]["fork"]["missed_count"] == 1
        assert data["by_theme"]["pin"]["missed_count"] == 0
        assert data["by_theme"]["king_safety"]["missed_count"] == 0

    def test_theme_pin_missed(self, client):
        src = "trainweak_pin"
        fen = "4k3/8/8/8/4n3/8/8/K6R w - - 0 1"
        with SessionLocal() as db:
            _seed_analysis_row(
                db, "twpin1", src, ply=30, player_color="white",
                fen_before=fen, played_uci="h1g1",  # non inchioda
                best_move_uci="h1e1",  # Re1 inchioda il cavallo al re
                classification="mistake", loss_cp=100, score_cp=-50,
            )
            db.commit()

        data = client.get("/training/weaknesses", params={"source": src}).json()
        assert data["by_theme"]["pin"]["missed_count"] == 1
        assert data["by_theme"]["fork"]["missed_count"] == 0

    def test_theme_king_safety_missed(self, client):
        src = "trainweak_king"
        fen = "6k1/8/8/8/8/8/5PPP/R5K1 w - - 0 1"
        with SessionLocal() as db:
            _seed_analysis_row(
                db, "twk1", src, ply=30, player_color="white",
                fen_before=fen, played_uci="g2g4",  # rompe lo scudo pedonale
                best_move_uci="a1a2",  # mossa neutra, scudo intatto
                classification="blunder", loss_cp=250, score_cp=-150,
            )
            db.commit()

        data = client.get("/training/weaknesses", params={"source": src}).json()
        assert data["by_theme"]["king_safety"]["missed_count"] == 1

    def test_themes_ignore_non_blunder_mistake_rows(self, client):
        """Le righe 'good'/'inaccuracy' non contribuiscono ai temi tattici
        (solo blunder/mistake, coerente con la generazione puzzle)."""
        src = "trainweak_good"
        fen = "r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1"
        with SessionLocal() as db:
            _seed_analysis_row(
                db, "twg1", src, ply=30, player_color="white",
                fen_before=fen, played_uci="b5c7",  # la stessa forchetta, ma...
                best_move_uci="b5c7",
                classification="good", loss_cp=0, score_cp=50,  # ...è la mossa "good"
            )
            db.commit()

        data = client.get("/training/weaknesses", params={"source": src}).json()
        assert data["by_theme"]["fork"]["missed_count"] == 0


# -------------------------------------------------------------------
# GET /training/endgames + POST /training/endgames/{id}/start
# -------------------------------------------------------------------

class TestTrainingEndgames:
    def test_list_endgames(self, client):
        r = client.get("/training/endgames")
        assert r.status_code == 200
        data = r.json()["endgames"]
        assert 15 <= len(data) <= 20
        ids = {d["id"] for d in data}
        # set minimo richiesto dalla spec (docs/training-mode.md)
        for required in ("kq_vs_k", "kr_vs_k", "kp_opposition_win", "lucena", "philidor"):
            assert required in ids
        for d in data:
            assert d["goal"] in ("win", "draw")
            assert chess.Board(d["fen"]).is_valid()  # ogni FEN dev'essere una posizione legale
            assert d["description"]

    def test_start_endgame_white_to_move(self, client):
        r = client.post(
            "/training/endgames/kr_vs_k/start",
            json={"player_color": "white", "engine_elo": 800},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["endgame_id"] == "kr_vs_k"
        assert data["goal"] == "win"
        assert data["fen"] == "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"
        assert data["move_history"] == []  # bianco al tratto, player bianco: nessuna mossa engine
        assert data["turn"] == "white"

        with SessionLocal() as db:
            row = db.get(Game, data["game_id"])
            assert row is not None
            assert row.source == "endgame_drill"
            assert row.start_fen == "4k3/8/8/8/8/8/8/R3K3 w - - 0 1"

    def test_start_endgame_black_to_move_no_engine_autoplay(self, client):
        """La FEN di philidor ha il NERO al tratto: se il player sceglie
        nero, l'engine non deve muovere per primo (fix Fase 4 di un bug
        latente in _create_new_game, vedi CLAUDE.md)."""
        r = client.post(
            "/training/endgames/philidor/start",
            json={"player_color": "black", "engine_elo": 800},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["turn"] == "black"
        assert data["move_history"] == []
        assert data["last_engine_move"] is None

    def test_start_endgame_engine_opens_when_color_mismatched(self, client):
        """Se il player sceglie un colore diverso da quello al tratto sulla
        FEN del drill, l'engine deve aprire la partita (coerente con
        POST /game/new per la posizione standard)."""
        r = client.post(
            "/training/endgames/philidor/start",  # nero al tratto
            json={"player_color": "white", "engine_elo": 800},
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["move_history"]) == 1
        assert data["last_engine_move"] is not None
        assert data["turn"] == "white"

    def test_start_endgame_unknown_id_404(self, client):
        r = client.post(
            "/training/endgames/does-not-exist/start",
            json={"player_color": "white", "engine_elo": 800},
        )
        assert r.status_code == 404

    def test_start_endgame_invalid_color(self, client):
        r = client.post(
            "/training/endgames/kr_vs_k/start",
            json={"player_color": "red", "engine_elo": 800},
        )
        assert r.status_code == 422


# -------------------------------------------------------------------
# Bug #8/#9 regression: POST /game/analyze su una partita con start_fen
# custom. Prima del fix, analyze_game() ignorava start_fen e la chiamata
# restava appesa a tempo indefinito (leak di processi Stockfish) su qualsiasi
# drill di finali o import PGN con header FEN. Vedi docs/bugs.md.
# -------------------------------------------------------------------
class TestAnalyzeStartFen:
    def test_analyze_endgame_drill_white_to_move(self, client):
        """kr_vs_k: FEN standard col Bianco al tratto — copre Bug #8 (analisi
        non deve appendersi/fallire) sul percorso più comune."""
        start = client.post(
            "/training/endgames/kr_vs_k/start",
            json={"player_color": "white", "engine_elo": 800},
        )
        assert start.status_code == 200
        gid = start.json()["game_id"]

        r = client.post("/game/move", json={"game_id": gid, "move_uci": "e1e2"})
        assert r.status_code == 200

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        analysis = r.json()
        assert analysis["total_moves"] >= 1
        ply1 = analysis["moves"][0]
        assert ply1["color"] == "white"
        assert ply1["move_number"] == 1
        assert ply1["move_uci"] == "e1e2"

    def test_analyze_endgame_drill_black_to_move_philidor(self, client):
        """Drill Philidor: FEN col Nero al tratto — copre sia Bug #8 (analisi
        di un drill deve funzionare, non appendersi) sia Bug #9 (move_number
        deve trattare il ply 1 come Nero, non assumere Bianco-first)."""
        start = client.post(
            "/training/endgames/philidor/start",
            json={"player_color": "black", "engine_elo": 800},
        )
        assert start.status_code == 200
        data = start.json()
        gid = data["game_id"]
        assert data["turn"] == "black"
        assert data["move_history"] == []  # nessun autoplay engine, nero già al tratto

        # Rxd2 — unica mossa che cattura il pedone bianco, dà scacco e forza
        # una risposta immediata dell'engine (stesso /game/move gestisce
        # entrambe le semimosse, come per una partita normale).
        r = client.post("/game/move", json={"game_id": gid, "move_uci": "d1d2"})
        assert r.status_code == 200
        move_data = r.json()
        assert len(move_data["move_history"]) == 2  # Nero + risposta Bianco

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        analysis = r.json()
        assert analysis["total_moves"] == 2

        ply1, ply2 = analysis["moves"]
        assert ply1["color"] == "black"
        assert ply1["move_uci"] == "d1d2"
        assert ply1["move_number"] == 1
        assert ply2["color"] == "white"
        assert ply2["move_number"] == 2

    def test_analyze_custom_start_fen_via_new_game(self, client):
        """POST /game/new con start_fen iniettato direttamente (non tramite
        drill), Bianco al tratto in una posizione custom non-standard: copre
        il secondo punto d'ingresso di start_fen menzionato in docs/bugs.md."""
        custom_fen = "8/8/8/8/8/8/4P3/4K2k w - - 0 1"
        r = client.post(
            "/game/new",
            json={"player_color": "white", "engine_elo": 800, "start_fen": custom_fen},
        )
        assert r.status_code == 200
        gid = r.json()["game_id"]

        r = client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
        assert r.status_code == 200

        r = client.post("/game/analyze", json={"game_id": gid, "depth": 8})
        assert r.status_code == 200
        analysis = r.json()
        assert analysis["total_moves"] >= 1
        ply1 = analysis["moves"][0]
        assert ply1["color"] == "white"
        assert ply1["move_number"] == 1
        assert ply1["move_uci"] == "e2e4"


class TestWebSocketLive:
    """Fase 6 — notifiche live di cambio stato via WebSocket (docs/websocket-live.md)."""

    def test_move_notifies_connected_socket(self, client, white_game):
        gid = white_game["game_id"]
        with client.websocket_connect(f"/ws/game/{gid}") as ws:
            r = client.post("/game/move", json={"game_id": gid, "move_uci": "e2e4"})
            assert r.status_code == 200
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert msg["game_id"] == gid
            # player + eventuale risposta engine → ply già consolidato
            assert msg["ply"] == len(r.json()["move_history"])
            assert msg["ply"] >= 1

    def test_multi_tab_both_receive(self, client, white_game):
        """Due connessioni indipendenti sulla stessa game_id: entrambe ricevono
        la notifica di una singola mossa (il caso multi-tab della roadmap)."""
        gid = white_game["game_id"]
        with client.websocket_connect(f"/ws/game/{gid}") as ws1, \
             client.websocket_connect(f"/ws/game/{gid}") as ws2:
            r = client.post("/game/move", json={"game_id": gid, "move_uci": "d2d4"})
            assert r.status_code == 200
            m1 = ws1.receive_json()
            m2 = ws2.receive_json()
            for m in (m1, m2):
                assert m["type"] == "state"
                assert m["game_id"] == gid

    def test_isolation_per_game_id(self, client):
        """Una connessione su A non riceve gli eventi di B: la prima notifica
        vista dalla socket di A è quella di A, non quella (precedente) di B."""
        a = client.post("/game/new", json={"player_color": "white", "engine_elo": 800}).json()
        b = client.post("/game/new", json={"player_color": "white", "engine_elo": 800}).json()
        gid_a, gid_b = a["game_id"], b["game_id"]
        with client.websocket_connect(f"/ws/game/{gid_a}") as ws_a:
            # Muovo prima su B (nessun subscriber di B), poi su A.
            assert client.post("/game/move", json={"game_id": gid_b, "move_uci": "e2e4"}).status_code == 200
            assert client.post("/game/move", json={"game_id": gid_a, "move_uci": "e2e4"}).status_code == 200
            msg = ws_a.receive_json()
            assert msg["type"] == "state"
            assert msg["game_id"] == gid_a

    def test_delete_notifies(self, client, white_game):
        gid = white_game["game_id"]
        with client.websocket_connect(f"/ws/game/{gid}") as ws:
            r = client.delete(f"/game/{gid}")
            assert r.status_code == 200
            msg = ws.receive_json()
            assert msg["type"] == "deleted"
            assert msg["game_id"] == gid

    def test_connect_unknown_game_id_ok(self, client):
        """Il socket è un canale di sola notifica: una game_id inesistente si
        connette senza errore (non riceverà mai nulla)."""
        with client.websocket_connect("/ws/game/deadbeef") as ws:
            assert ws is not None
