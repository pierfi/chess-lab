"""Test end-to-end per Chess Lab API."""

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import app, games
from backend.db import AnalysisResult, SessionLocal, Game, Move, utcnow


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
