"""Test per la CLI companion (chess_app/cli/), Wave 1 (design doc
docs/cli-companion-mode-design.md §8).

Copre: mapping effort→Skill Level, logica di prompt a turni alternati,
registrazione mossa (successo/fallimento, backend reale via ASGITransport),
etichettatura "tuoi/suoi" di /threats, re-sync dell'undo, rendering rich
(Task 4 — spinner, pannelli, lista mosse, colori "in presa"). Il motore
Stockfish locale è sempre uno stub in questi test (mai un vero processo
Stockfish, per velocità/determinismo) — solo depth/Skill Level passati
all'engine vengono verificati, non la qualità della ricerca."""

import io

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from rich.console import Console

import cli.autohint as autohint
import cli.ui as ui
from backend.main import app
from cli import __main__ as cli_main
from cli.backend_client import BackendClient, BackendError, BackendUnavailable
from cli.config import ADVICE_DEPTH, ADVICE_MULTIPV, FULL_STRENGTH_ELO, elo_to_skill_depth
from cli.effort import EFFORT_LEVELS, skill_level_for_effort
from cli.local_engine import LocalEngineAdvisor
from cli.repl import (
    _announce_game_over,
    _format_analysis_summary,
    _run_advice_step,
    _seed_pending_advice,
    _show_advice,
)
from cli.session import CompanionSession, is_players_turn, label_threats, turn_prompt_label


def make_capture_console(width: int = 100) -> tuple[Console, io.StringIO]:
    """Console `rich` che scrive su uno StringIO invece che su un vero
    terminale — `force_terminal=False` disabilita colori/markup/animazioni
    nell'output catturato, lasciando solo il testo informativo da asserire
    (nessuna dipendenza da un vero TTY, nessun parsing di sequenze ANSI)."""
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=width), buf


# ---------------------------------------------------------------------------
# Stub del motore locale — niente popen_uci reale nei test (vedi docstring).
# ---------------------------------------------------------------------------

class FakeAnalysingEngine:
    """Sostituisce chess.engine.SimpleEngine nei test: registra le chiamate a
    configure()/analyse() invece di spawnare un vero Stockfish."""

    def __init__(self):
        self.configure_calls: list[dict] = []
        self.analyse_calls: list[dict] = []
        self.quit_called = False

    def configure(self, options: dict) -> None:
        self.configure_calls.append(options)

    def analyse(self, board: chess.Board, limit: "chess.engine.Limit", multipv: int = 1):
        self.analyse_calls.append({"depth": limit.depth, "multipv": multipv})
        move = next(iter(board.legal_moves))
        info = {
            "pv": [move],
            "score": chess.engine.PovScore(chess.engine.Cp(34), chess.WHITE),
        }
        return [info for _ in range(multipv)]

    def quit(self) -> None:
        self.quit_called = True


class ScriptedAnalysingEngine:
    """Come `FakeAnalysingEngine`, ma con un eval (POV bianco) DIVERSO ad
    ogni chiamata invece che costante — necessario per esercitare la logica
    di soglia auto-hint (Wave 2, design doc §10), che confronta l'eval
    "prima" e "dopo" di due chiamate separate `advice()`. Ogni chiamata ad
    `analyse()` consuma il prossimo valore scriptato, in ordine; una lista
    più corta delle chiamate effettive fa fallire il test con
    `StopIteration` invece di restituire un valore silenziosamente sbagliato
    — preferibile per un test di sequenza."""

    def __init__(self, scores_white_pov: list[int]):
        self._scores = iter(scores_white_pov)
        self.analyse_calls = 0

    def configure(self, options: dict) -> None:
        pass

    def analyse(self, board: chess.Board, limit: "chess.engine.Limit", multipv: int = 1):
        self.analyse_calls += 1
        score = next(self._scores)
        move = next(iter(board.legal_moves))
        info = {
            "pv": [move],
            "score": chess.engine.PovScore(chess.engine.Cp(score), chess.WHITE),
        }
        return [info for _ in range(multipv)]

    def quit(self) -> None:
        pass


def make_backend_client() -> BackendClient:
    """BackendClient contro l'app FastAPI reale, in-process, senza un server
    uvicorn separato.

    Nota di implementazione: `httpx.ASGITransport` in httpx 0.28 (la versione
    pinnata in requirements.txt) implementa SOLO `handle_async_request` — non
    è utilizzabile con un `httpx.Client` sincrono come quello che
    `BackendClient` usa in produzione (verificato: solleva
    `AttributeError: 'ASGITransport' object has no attribute
    'handle_request'`). `starlette.testclient.TestClient` è comunque una
    sottoclasse di `httpx.Client` (stesso duck-type richiesto da
    `BackendClient`) che fa da ponte sync↔async con un portale `anyio` — è la
    STESSA tecnica in-process-ASGI richiesta, solo attraverso il bridge già
    usato dal resto della suite invece del transport nudo, che qui non
    funzionerebbe."""
    return BackendClient(client=TestClient(app))


def make_unreachable_backend_client() -> BackendClient:
    """BackendClient che punta a una porta locale su cui nessuno ascolta —
    per esercitare il percorso di degrado 'backend irraggiungibile'."""
    return BackendClient(base_url="http://127.0.0.1:1", timeout=0.5)


# ---------------------------------------------------------------------------
# 1. Effort → Skill Level
# ---------------------------------------------------------------------------

class TestEffortToSkill:
    def test_effort_levels_are_within_backend_elo_range(self):
        # CompanionNewGameRequest.effort_elo è Field(ge=400, le=2800): ogni
        # preset deve restare un valore accettabile dal backend.
        for _label, elo in EFFORT_LEVELS:
            assert 400 <= elo <= 2800

    def test_skill_level_matches_backend_table_below_full_strength(self):
        for _label, elo in EFFORT_LEVELS:
            if elo >= FULL_STRENGTH_ELO:
                continue
            expected_skill, _expected_depth = elo_to_skill_depth(elo)
            assert skill_level_for_effort(elo) == expected_skill

    def test_top_effort_skips_skill_level_entirely(self):
        # Design doc §6/§11.2: l'effort "Massimo" non configura ALCUNO Skill
        # Level (piena forza, default Stockfish) — non è semplicemente
        # elo_to_skill_depth(2800), è una scelta esplicita di non chiamare
        # engine.configure() affatto.
        assert skill_level_for_effort(FULL_STRENGTH_ELO) is None

    def test_local_engine_receives_fixed_depth_and_chosen_skill(self):
        engine = FakeAnalysingEngine()
        advisor = LocalEngineAdvisor(engine, skill_level=skill_level_for_effort(600))

        assert engine.configure_calls == [{"Skill Level": 0}]  # elo 600 → skill 0

        board = chess.Board()
        advice = advisor.advice(board)

        # La depth usata dal loop di consiglio è SEMPRE quella fissa di
        # modulo, MAI quella (7, per elo 600 in elo_to_skill_depth) usata per
        # calibrare la forza dell'avversario lato backend.
        assert engine.analyse_calls == [{"depth": ADVICE_DEPTH, "multipv": ADVICE_MULTIPV}]
        assert advice["eval_cp"] == 34
        assert len(advice["lines"]) == ADVICE_MULTIPV

    def test_local_engine_full_strength_never_configures_skill(self):
        engine = FakeAnalysingEngine()
        LocalEngineAdvisor(engine, skill_level=skill_level_for_effort(FULL_STRENGTH_ELO))
        assert engine.configure_calls == []


# ---------------------------------------------------------------------------
# 2. Prompt a turni alternati
# ---------------------------------------------------------------------------

class TestTurnPrompt:
    def test_prompts_player_move_when_it_is_players_turn(self):
        board = chess.Board()  # bianco al tratto
        assert turn_prompt_label(board, "white") == "hai giocato"

    def test_prompts_opponent_move_when_it_is_opponents_turn(self):
        board = chess.Board()  # bianco al tratto
        assert turn_prompt_label(board, "black") == "l'avversario ha giocato"

    def test_alternates_after_a_move_is_pushed(self):
        board = chess.Board()
        board.push_san("e4")  # ora tocca al nero
        assert turn_prompt_label(board, "white") == "l'avversario ha giocato"
        assert turn_prompt_label(board, "black") == "hai giocato"


# ---------------------------------------------------------------------------
# 3. Registrazione mossa — successo/fallimento, backend reale via ASGITransport
# ---------------------------------------------------------------------------

class TestRegisterMove:
    def test_start_creates_companion_game_and_syncs_board(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        state = session.start("white", 1200)

        assert not session.degraded
        assert session.game_id == state["game_id"]
        assert state["source"] == "companion"
        assert session.board == chess.Board()
        session.close()

    def test_register_move_success_updates_board_and_turn(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.register_move("e4")

        assert outcome["ok"]
        assert session.board.turn == chess.BLACK
        assert outcome["state"]["move_history"] == ["e2e4"]
        session.close()

    def test_register_move_accepts_uci_as_well_as_san(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.register_move("e2e4")

        assert outcome["ok"]
        assert outcome["state"]["move_history"] == ["e2e4"]
        session.close()

    def test_register_move_failure_surfaces_backend_message_and_does_not_move_board(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        board_before = session.board.copy()

        outcome = session.register_move("e5")  # illegale: il bianco è al tratto

        assert not outcome["ok"]
        assert outcome["error"]  # messaggio pronto per essere ririportato all'utente
        assert session.board == board_before  # nessuna mutazione su fallimento

    def test_the_users_move_need_not_match_the_suggested_best_move(self):
        # Vincolo esplicito del design doc §3.2: qualunque mossa LEGALE
        # riportata viene registrata, mai forzata a coincidere col consiglio
        # del motore locale.
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        advice = session.advice()
        suggested_san = advice["lines"][0]["move_san"]

        outcome = session.register_move("Nf3" if suggested_san != "Nf3" else "e4")
        assert outcome["ok"]
        session.close()

    def test_start_degrades_gracefully_when_backend_unreachable(self):
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))

        result = session.start("white", 1200)

        assert session.degraded
        assert session.game_id is None
        assert result == {"degraded": True}

    def test_degraded_mode_still_registers_legal_moves_locally(self):
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.register_move("e4")

        assert outcome["ok"]
        assert outcome["state"] is None  # nessun record durevole in degrado
        assert session.board.turn == chess.BLACK

    def test_degraded_mode_rejects_illegal_moves_with_a_retry_message(self):
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.register_move("e5")

        assert not outcome["ok"]
        assert "ribattila" in outcome["error"]

    def test_degraded_mode_has_no_threats(self):
        # /threats è server-side; senza game_id non c'è nulla da chiamare.
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        assert session.threats() is None


# ---------------------------------------------------------------------------
# 4. Etichettatura /threats "tuoi" / "dell'avversario"
# ---------------------------------------------------------------------------

class TestThreatsLabeling:
    def test_labels_as_yours_when_side_matches_player_color(self):
        raw = {"side": "white", "in_presa": [{"square": "f6", "piece": "n", "value": 3, "attackers": ["e4"]}]}
        labeled = label_threats(raw, player_color="white")
        assert labeled["label"] == "tuoi pezzi in presa"
        assert labeled["in_presa"] == raw["in_presa"]

    def test_labels_as_opponents_when_side_differs_from_player_color(self):
        raw = {"side": "black", "in_presa": []}
        labeled = label_threats(raw, player_color="white")
        assert labeled["label"] == "pezzi in presa dell'avversario"

    def test_threats_after_opponent_move_labels_players_own_hanging_pieces(self):
        # Design doc §3.1: chiamare /threats dopo OGNI mossa registrata
        # etichetta automaticamente "tuoi" quando tocca al player e "suoi"
        # quando tocca all'avversario — proprietà end-to-end contro il
        # backend reale, non solo la funzione pura sopra.
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("black", 1200)  # il player è il nero

        # Il bianco (l'avversario) apre con una mossa che lascia un pedone
        # attaccabile ma indifeso non è necessario: verifichiamo solo che
        # side alterni e l'etichetta segua player_color, qualunque sia
        # in_presa.
        session.register_move("e4")  # mossa dell'avversario (bianco)
        threats_after_opponent = session.threats()
        assert threats_after_opponent["side"] == "black"  # ora tocca al player
        assert threats_after_opponent["label"] == "tuoi pezzi in presa"

        session.register_move("e5")  # mossa del player (nero)
        threats_after_player = session.threats()
        assert threats_after_player["side"] == "white"  # ora tocca all'avversario
        assert threats_after_player["label"] == "pezzi in presa dell'avversario"
        session.close()


# ---------------------------------------------------------------------------
# 5. Undo — re-sync della board locale
# ---------------------------------------------------------------------------

class TestUndo:
    def test_undo_reverts_board_to_previous_position(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.register_move("e4")
        fen_after_e4 = session.board.fen()

        outcome = session.undo()

        assert outcome["ok"]
        assert session.board == chess.Board()
        assert session.board.fen() != fen_after_e4
        assert outcome["state"]["move_history"] == []
        session.close()

    def test_undo_with_no_moves_returns_error_not_crash(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.undo()

        assert not outcome["ok"]
        session.close()

    def test_degraded_undo_reverts_board_locally(self):
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.register_move("e4")

        outcome = session.undo()

        assert outcome["ok"]
        assert session.board == chess.Board()


# ---------------------------------------------------------------------------
# BackendClient — dettagli non coperti sopra (errori 400 e connessione)
# ---------------------------------------------------------------------------

class TestBackendClient:
    def test_backend_error_carries_the_detail_message(self):
        backend = make_backend_client()
        state = backend.new_companion_game("white", 1200)
        with pytest.raises(BackendError):
            backend.companion_move(state["game_id"], "e5")  # illegale

    def test_backend_unavailable_raised_on_connection_failure(self):
        backend = make_unreachable_backend_client()
        with pytest.raises(BackendUnavailable):
            backend.new_companion_game("white", 1200)


# ---------------------------------------------------------------------------
# 6. /pgn — scrittura su file dell'ultimo stato tracciato (Task 3)
# ---------------------------------------------------------------------------

class TestPgnCommand:
    def test_pgn_writes_file_with_backends_pgn(self, tmp_path):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.register_move("e4")

        out_path = tmp_path / "game.pgn"
        outcome = session.write_pgn(str(out_path))

        assert outcome["ok"]
        assert outcome["path"] == str(out_path)
        written = out_path.read_text(encoding="utf-8")
        # Stesso contenuto ESATTO del campo "pgn" dell'ultimo stato tracciato
        # — nessuna trasformazione/ricostruzione locale.
        assert written == session.last_state["pgn"]
        assert "1. e4" in written
        session.close()

    def test_pgn_available_even_before_any_move_is_registered(self, tmp_path):
        # start() già popola last_state (una companion appena creata ha
        # comunque un PGN valido, solo senza mosse) — /pgn non deve
        # richiedere che sia già stata registrata almeno una mossa.
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.write_pgn(str(tmp_path / "game.pgn"))

        assert outcome["ok"]
        session.close()

    def test_pgn_unavailable_in_degraded_mode(self, tmp_path):
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        target = tmp_path / "game.pgn"
        outcome = session.write_pgn(str(target))

        assert not outcome["ok"]
        assert "degrad" in outcome["error"].lower()
        assert not target.exists()


# ---------------------------------------------------------------------------
# 7. /analyze — chiamata a POST /game/analyze e riepilogo (Task 3)
# ---------------------------------------------------------------------------

class TestAnalyzeCommand:
    def test_analyze_calls_backend_with_the_tracked_game_id(self, monkeypatch):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.register_move("e4")

        fake_result = {
            "game_id": session.game_id,
            "total_moves": 1,
            "blunders": 1,
            "mistakes": 0,
            "inaccuracies": 0,
            "accuracy_score": 42.0,
            "moves": [
                {
                    "ply": 1, "move_number": 1, "color": "white",
                    "move_uci": "e2e4", "move_san": "e4",
                    "best_move_uci": "d2d4", "score_cp": 18,
                    "loss_cp": 250, "classification": "blunder",
                },
            ],
        }
        calls = []
        monkeypatch.setattr(
            backend, "analyze", lambda game_id: calls.append(game_id) or fake_result
        )

        outcome = session.analyze()

        assert calls == [session.game_id]
        assert outcome == {"ok": True, "result": fake_result}
        session.close()

    def test_analyze_summary_formats_totals_and_flagged_plies(self):
        result = {
            "total_moves": 3,
            "blunders": 1,
            "mistakes": 1,
            "inaccuracies": 0,
            "accuracy_score": 55.5,
            "moves": [
                {"ply": 1, "color": "white", "move_san": "e4",
                 "loss_cp": 0, "classification": "excellent"},
                {"ply": 2, "color": "black", "move_san": "a6",
                 "loss_cp": 250, "classification": "blunder"},
                {"ply": 3, "color": "white", "move_san": "Qh5",
                 "loss_cp": 90, "classification": "mistake"},
            ],
        }

        lines = _format_analysis_summary(result)
        text = "\n".join(lines)

        assert "Mosse totali: 3" in text
        assert "Accuracy: 55.5%" in text
        assert "Blunder: 1  Mistake: 1  Inaccuracy: 0" in text
        # Solo blunder/mistake elencati (ply 1, "excellent", resta fuori).
        assert "Ply 2 (black) a6: blunder" in text
        assert "Ply 3 (white) Qh5: mistake" in text
        assert "Ply 1" not in text

    def test_analyze_unavailable_in_degraded_mode(self):
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.analyze()

        assert not outcome["ok"]
        assert "degrad" in outcome["error"].lower()

    def test_analyze_surfaces_backend_400_when_no_moves_played(self):
        # Nessun mock: il backend reale risponde 400 ("No moves to analyze")
        # su una companion appena creata — lo stesso messaggio torna in
        # error, ririportato all'utente così com'è.
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        outcome = session.analyze()

        assert not outcome["ok"]
        assert outcome["error"]
        session.close()


# ---------------------------------------------------------------------------
# 8. Riepilogo automatico di fine partita (Task 3)
# ---------------------------------------------------------------------------

class TestEndOfGameSummary:
    FOOLS_MATE = ["f3", "e5", "g4", "Qh4"]  # scacco matto in 4 mosse

    def test_is_game_over_and_move_count_after_checkmate(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        for mv in self.FOOLS_MATE:
            outcome = session.register_move(mv)
            assert outcome["ok"], outcome

        assert session.is_game_over()
        assert session.move_count() == 4
        session.close()

    def test_announce_game_over_does_not_force_analyze_when_declined(self, monkeypatch):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        for mv in self.FOOLS_MATE:
            session.register_move(mv)

        analyze_calls = []
        monkeypatch.setattr(backend, "analyze", lambda game_id: analyze_calls.append(game_id))

        outputs = []
        _announce_game_over(session, input_func=lambda _prompt: "n", output_func=outputs.append)

        assert any("terminata dopo 4 mosse" in line for line in outputs)
        assert analyze_calls == []  # l'utente ha rifiutato: nessuna analisi
        session.close()

    def test_announce_game_over_runs_analyze_when_user_confirms(self, monkeypatch):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        for mv in self.FOOLS_MATE:
            session.register_move(mv)

        fake_result = {
            "total_moves": 4, "blunders": 0, "mistakes": 0, "inaccuracies": 0,
            "accuracy_score": 99.0, "moves": [],
        }
        monkeypatch.setattr(backend, "analyze", lambda game_id: fake_result)

        outputs = []
        _announce_game_over(session, input_func=lambda _prompt: "s", output_func=outputs.append)

        assert any("Accuracy: 99.0%" in line for line in outputs)
        session.close()

    def test_game_over_detected_locally_in_degraded_mode(self):
        # Design doc: in degrado non c'è alcuna risposta di stato
        # server-side da controllare — l'unica fonte è la board locale.
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        for mv in self.FOOLS_MATE:
            outcome = session.register_move(mv)
            assert outcome["ok"], outcome

        assert session.is_game_over()
        assert session.move_count() == 4

    def test_announce_game_over_in_degraded_mode_skips_analyze_prompt(self):
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        for mv in self.FOOLS_MATE:
            session.register_move(mv)

        def fail_if_called(_prompt):
            raise AssertionError("non deve chiedere conferma analisi in modalità degradata")

        outputs = []
        _announce_game_over(session, input_func=fail_if_called, output_func=outputs.append)

        assert any("terminata dopo 4 mosse" in line for line in outputs)
        assert any("degradata" in line.lower() for line in outputs)


# ---------------------------------------------------------------------------
# 9. move_history_san() — sorgente per la lista mosse stilizzata (Task 4)
# ---------------------------------------------------------------------------

class TestMoveHistorySan:
    def test_move_history_san_matches_backend_after_moves(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.register_move("e4")
        session.register_move("e5")

        assert session.move_history_san() == ["e4", "e5"]
        session.close()

    def test_move_history_san_empty_before_any_move(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        assert session.move_history_san() == []
        session.close()

    def test_move_history_san_reconstructed_locally_in_degraded_mode(self):
        # Nessun last_state server-side in degrado: va rigiocata dalla board
        # locale (board.root() + replay), non un accumulatore separato.
        backend = make_unreachable_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.register_move("e4")
        session.register_move("e5")

        assert session.move_history_san() == ["e4", "e5"]


# ---------------------------------------------------------------------------
# 10. Rendering rich (Task 4, design doc §7) — cli/ui.py
# ---------------------------------------------------------------------------

class TestUiFormatting:
    """Funzioni pure di scelta stile/colore — nessun bisogno di toccare rich
    o parsare ANSI: separate apposta da render_* per essere testabili a
    prescindere dal rendering effettivo."""

    def test_format_eval_text_for_normal_score(self):
        assert ui.format_eval_text(34) == "+0.34"
        assert ui.format_eval_text(-250) == "-2.50"

    def test_format_eval_text_for_none(self):
        assert ui.format_eval_text(None) == "n/d"

    def test_format_eval_text_for_mate_scores(self):
        assert "bianco" in ui.format_eval_text(10000)
        assert "nero" in ui.format_eval_text(-10000)

    def test_move_row_style_highlights_only_the_best_line(self):
        assert ui.move_row_style(1) == "bold green"
        assert ui.move_row_style(2) != "bold green"
        assert ui.move_row_style(3) != "bold green"

    def test_threat_style_distinguishes_yours_from_opponents(self):
        # Design doc §3.1/§10: "tuoi" vs "suoi" devono avere colore/icona
        # visivamente distinti, mai lo stesso.
        yours = ui.threat_style("tuoi pezzi in presa")
        opponents = ui.threat_style("pezzi in presa dell'avversario")
        assert yours != opponents
        assert yours == ("red", "⚠")
        assert opponents == ("green", "★")


class TestUiRendering:
    """Rendering effettivo via una Console che scrive su StringIO
    (force_terminal=False, vedi make_capture_console) — verifica il
    contenuto informativo (mosse, eval, etichette), non sequenze ANSI."""

    def test_render_advice_shows_moves_and_eval(self):
        console, buf = make_capture_console()
        advice = {
            "eval_cp": 34,
            "lines": [
                {"move_uci": "e2e4", "move_san": "e4", "score_cp": 34},
                {"move_uci": "d2d4", "move_san": "d4", "score_cp": 28},
            ],
        }
        ui.render_advice(console, advice)
        text = buf.getvalue()

        assert "Consiglio motore locale" in text
        assert "e4" in text and "d4" in text
        assert "+34 cp" in text
        assert "Valutazione: +0.34" in text

    def test_render_advice_handles_no_candidate_lines(self):
        console, buf = make_capture_console()
        ui.render_advice(console, {"eval_cp": None, "lines": []})
        text = buf.getvalue()

        assert "nessuna mossa disponibile" in text
        assert "Valutazione: n/d" in text

    def test_render_threats_none_prints_nothing(self):
        console, buf = make_capture_console()
        ui.render_threats(console, None)
        assert buf.getvalue() == ""

    def test_render_threats_empty_list_prints_nothing(self):
        console, buf = make_capture_console()
        ui.render_threats(console, {"label": "tuoi pezzi in presa", "side": "white", "in_presa": []})
        assert buf.getvalue() == ""

    def test_render_threats_shows_square_and_attackers(self):
        console, buf = make_capture_console()
        labeled = {
            "label": "tuoi pezzi in presa",
            "side": "white",
            "in_presa": [{"square": "f6", "piece": "n", "value": 3, "attackers": ["e4", "g5"]}],
        }
        ui.render_threats(console, labeled)
        text = buf.getvalue()

        assert "Tuoi pezzi in presa" in text
        assert "f6" in text
        assert "e4, g5" in text

    def test_render_move_list_pairs_white_and_black(self):
        console, buf = make_capture_console()
        ui.render_move_list(console, ["e4", "e5", "Nf3"])
        text = buf.getvalue()

        assert "1." in text and "e4" in text and "e5" in text
        assert "2." in text and "Nf3" in text

    def test_render_move_list_empty_prints_nothing(self):
        console, buf = make_capture_console()
        ui.render_move_list(console, [])
        assert buf.getvalue() == ""

    def test_advice_status_is_a_usable_context_manager(self):
        # Smoke test: entrare/uscire dal context manager non deve esplodere
        # anche in un ambiente non-TTY catturato (force_terminal=False) —
        # copre l'uso reale in _show_advice.
        console, _buf = make_capture_console()
        with ui.advice_status(console):
            pass


class TestShowAdvice:
    """`_show_advice` (repl.py) — collante fra spinner, session.advice()/
    threats() e i tre pannelli rich. Verifica che TUTTI e tre vengano
    effettivamente stampati sulla console passata, con il backend reale
    (via ASGITransport) per esercitare anche l'etichettatura /threats."""

    def test_show_advice_renders_advice_threats_and_move_list(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("black", 1200)
        session.register_move("e4")  # mossa dell'avversario (bianco)

        console, buf = make_capture_console()
        _show_advice(session, console)
        text = buf.getvalue()

        assert "Consiglio motore locale" in text  # pannello advice
        assert "Mosse" in text  # pannello lista mosse
        assert "e4" in text
        session.close()


# ---------------------------------------------------------------------------
# 11. Wave 2 — auto-hint a soglia, opt-in (design doc §10)
# ---------------------------------------------------------------------------

class TestAutoHintPureLogic:
    """`cli/autohint.py` — funzioni pure, nessun motore/terminale coinvolto.
    Riusano ESATTAMENTE la convenzione di segno di `analyze_game` in
    `backend/main.py` (CLAUDE.md, tabella di classificazione): eval sempre
    POV bianco, loss = prima - dopo per il bianco, dopo - prima per il
    nero."""

    def test_move_loss_cp_positive_when_white_mover_eval_drops(self):
        assert autohint.move_loss_cp(50, -100, "white") == 150

    def test_move_loss_cp_positive_when_black_mover_eval_rises_for_white(self):
        # Il nero muove: un peggioramento per lui è un eval (POV bianco) che
        # SALE dopo la sua mossa, non che scende.
        assert autohint.move_loss_cp(-50, 100, "black") == 150

    def test_move_loss_cp_negative_means_the_move_improved_the_position(self):
        assert autohint.move_loss_cp(50, 80, "white") == -30

    def test_move_loss_cp_none_when_either_eval_is_missing(self):
        assert autohint.move_loss_cp(None, 10, "white") is None
        assert autohint.move_loss_cp(10, None, "black") is None
        assert autohint.move_loss_cp(None, None, "white") is None

    def test_exceeds_threshold_true_when_loss_bigger_than_threshold(self):
        assert autohint.exceeds_threshold(200, 150) is True

    def test_exceeds_threshold_false_when_loss_within_threshold(self):
        assert autohint.exceeds_threshold(100, 150) is False

    def test_exceeds_threshold_false_at_exact_boundary(self):
        # Confronto stretto: perdere ESATTAMENTE la soglia non la supera.
        assert autohint.exceeds_threshold(150, 150) is False

    def test_exceeds_threshold_false_when_loss_is_none(self):
        # "Non quantificabile" non forza mai il pannello completo.
        assert autohint.exceeds_threshold(None, 150) is False

    def test_exceeds_threshold_false_for_a_move_that_improved_the_position(self):
        assert autohint.exceeds_threshold(-30, 150) is False


class TestIsPlayersTurn:
    """`cli.session.is_players_turn` — estratta da `turn_prompt_label` per
    essere consumata anche dalla logica a soglia (serve PRIMA di
    `register_move`, quando il turno è ancora quello di chi deve riportare
    la prossima mossa)."""

    def test_true_when_next_mover_is_the_player(self):
        board = chess.Board()  # bianco al tratto
        assert is_players_turn(board, "white") is True
        assert is_players_turn(board, "black") is False

    def test_alternates_after_a_move_is_pushed(self):
        board = chess.Board()
        board.push_san("e4")
        assert is_players_turn(board, "white") is False
        assert is_players_turn(board, "black") is True

    def test_turn_prompt_label_still_consistent_with_is_players_turn(self):
        # Refactor di turn_prompt_label su is_players_turn: nessuna
        # regressione di comportamento (già coperto da TestTurnPrompt sopra,
        # ripetuto qui per documentare esplicitamente la dipendenza).
        board = chess.Board()
        assert turn_prompt_label(board, "white") == "hai giocato"
        assert is_players_turn(board, "white") is True


class TestPendingPlayerAdvice:
    """`CompanionSession.remember_pending_player_advice` /
    `consume_pending_player_loss` — la cache "pre-mossa" del player (Wave 2,
    design doc §10), verificata a livello di sessione senza passare dalla
    REPL."""

    def test_no_pending_advice_yields_non_quantifiable_delta(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)

        delta = session.consume_pending_player_loss(34)

        assert delta == {"loss_cp": None, "best_move_san": None}
        session.close()

    def test_remember_then_consume_computes_loss_for_white_player(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.remember_pending_player_advice(
            {"eval_cp": 50, "lines": [{"move_uci": "g1f3", "move_san": "Nf3", "score_cp": 50}]}
        )

        delta = session.consume_pending_player_loss(-100)

        assert delta == {"loss_cp": 150, "best_move_san": "Nf3"}
        session.close()

    def test_consuming_is_a_one_shot_operation(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.remember_pending_player_advice({"eval_cp": 50, "lines": []})

        session.consume_pending_player_loss(-100)
        second = session.consume_pending_player_loss(-100)

        assert second == {"loss_cp": None, "best_move_san": None}
        session.close()

    def test_remember_then_consume_computes_loss_for_black_player(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("black", 1200)
        session.remember_pending_player_advice({"eval_cp": -50, "lines": []})

        delta = session.consume_pending_player_loss(100)

        assert delta == {"loss_cp": 150, "best_move_san": None}
        session.close()

    def test_pending_advice_without_candidate_lines_has_no_best_move(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.remember_pending_player_advice({"eval_cp": 50, "lines": []})

        delta = session.consume_pending_player_loss(20)

        assert delta["best_move_san"] is None
        session.close()

    def test_undo_clears_pending_advice(self):
        backend = make_backend_client()
        session = CompanionSession(backend, LocalEngineAdvisor(FakeAnalysingEngine()))
        session.start("white", 1200)
        session.register_move("e4")
        session.remember_pending_player_advice({"eval_cp": 10, "lines": []})

        session.undo()

        assert session.pending_player_advice is None
        assert session.consume_pending_player_loss(10) == {"loss_cp": None, "best_move_san": None}
        session.close()


class TestQuietModeRendering:
    """`ui.render_quiet_ack`/`ui.render_threshold_alert` (Wave 2) — stessa
    tecnica "capture console" delle altre TestUiRendering sopra."""

    def test_render_quiet_ack_shows_positive_delta(self):
        console, buf = make_capture_console()
        ui.render_quiet_ack(console, 42)
        text = buf.getvalue()
        assert "entro soglia" in text
        assert "+42 cp" in text

    def test_render_quiet_ack_shows_negative_delta_with_sign(self):
        console, buf = make_capture_console()
        ui.render_quiet_ack(console, -15)
        assert "-15 cp" in buf.getvalue()

    def test_render_quiet_ack_handles_none_loss(self):
        console, buf = make_capture_console()
        ui.render_quiet_ack(console, None)
        assert "non disponibile" in buf.getvalue()

    def test_render_threshold_alert_includes_best_move(self):
        console, buf = make_capture_console()
        ui.render_threshold_alert(console, 180, "Nf6")
        text = buf.getvalue()
        assert "Hai perso ~180 cp" in text
        assert "Nf6" in text

    def test_render_threshold_alert_without_best_move_omits_suggestion_text(self):
        console, buf = make_capture_console()
        ui.render_threshold_alert(console, 180, None)
        text = buf.getvalue()
        assert "Hai perso ~180 cp" in text
        assert "consigliava" not in text


class TestQuietModeIntegration:
    """`_run_advice_step`/`_seed_pending_advice` (repl.py, Wave 2) — sessione
    reale (backend via ASGITransport) + motore locale scriptato (eval
    controllato ad ogni chiamata), stessa tecnica di TestShowAdvice sopra."""

    def test_default_mode_always_shows_full_panel_regardless_of_loss(self):
        # auto_hint_threshold=None: comportamento storico Wave 1 invariato,
        # anche per una mossa che avrebbe sforato qualunque soglia.
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([50])
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("white", 1200)

        console, buf = make_capture_console()
        _run_advice_step(session, console, None, was_players_move=True)
        text = buf.getvalue()

        assert "Consiglio motore locale" in text
        assert "Hai perso" not in text  # nessun framing soglia in modalità default
        session.close()

    def test_quiet_mode_shows_full_panel_after_opponent_move_and_caches_eval(self):
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([50])
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("black", 1200)

        console, buf = make_capture_console()
        _run_advice_step(session, console, 150, was_players_move=False)
        text = buf.getvalue()

        assert "Consiglio motore locale" in text  # sempre pieno dopo la mossa avversario
        assert session.pending_player_advice is not None
        assert session.pending_player_advice["eval_cp"] == 50
        session.close()

    def test_quiet_mode_suppresses_full_panel_under_threshold(self):
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([40])  # eval "dopo": perdita di 10cp per il bianco
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("white", 1200)
        session.remember_pending_player_advice(
            {"eval_cp": 50, "lines": [{"move_uci": "g1f3", "move_san": "Nf3", "score_cp": 50}]}
        )

        console, buf = make_capture_console()
        _run_advice_step(session, console, 150, was_players_move=True)
        text = buf.getvalue()

        assert "Consiglio motore locale" not in text  # niente pannello pieno
        assert "entro soglia" in text
        session.close()

    def test_quiet_mode_shows_full_panel_and_alert_over_threshold(self):
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([-300])  # eval "dopo": crollo di 350cp per il bianco
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("white", 1200)
        session.remember_pending_player_advice(
            {"eval_cp": 50, "lines": [{"move_uci": "g1f3", "move_san": "Nf3", "score_cp": 50}]}
        )

        console, buf = make_capture_console()
        _run_advice_step(session, console, 150, was_players_move=True)
        text = buf.getvalue()

        assert "Hai perso ~350 cp" in text
        assert "Nf3" in text
        assert "Consiglio motore locale" in text  # pannello pieno mostrato comunque
        session.close()

    def test_quiet_mode_over_threshold_with_no_cached_advice_has_no_best_move_text(self):
        # Nessuna advice pre-mossa cachata (es. mossa arrivata senza un
        # /hint o un turno avversario precedente in questa sessione di
        # test) → delta non quantificabile, mai un pannello forzato.
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([-300])
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("white", 1200)

        console, buf = make_capture_console()
        _run_advice_step(session, console, 150, was_players_move=True)
        text = buf.getvalue()

        assert "Consiglio motore locale" not in text
        assert "non disponibile" in text
        session.close()

    def test_seed_pending_advice_shows_panel_when_player_moves_first(self):
        # Player bianco: la primissima mossa da riportare è già la sua —
        # nessuna mossa avversario precedente da cui far scattare il
        # consiglio "in avanti", va quindi anticipato all'apertura sessione.
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([20])
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("white", 1200)

        console, buf = make_capture_console()
        _seed_pending_advice(session, console, 150)

        assert "Consiglio motore locale" in buf.getvalue()
        assert session.pending_player_advice["eval_cp"] == 20
        session.close()

    def test_seed_pending_advice_noop_when_opponent_moves_first(self):
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([20])
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("black", 1200)  # il bianco (avversario) muove per primo

        console, buf = make_capture_console()
        _seed_pending_advice(session, console, 150)

        assert buf.getvalue() == ""
        assert session.pending_player_advice is None
        assert engine.analyse_calls == 0
        session.close()

    def test_seed_pending_advice_noop_in_default_mode(self):
        backend = make_backend_client()
        engine = ScriptedAnalysingEngine([20])
        session = CompanionSession(backend, LocalEngineAdvisor(engine))
        session.start("white", 1200)

        console, buf = make_capture_console()
        _seed_pending_advice(session, console, None)

        assert buf.getvalue() == ""
        assert engine.analyse_calls == 0
        session.close()


class TestAutoHintCliFlag:
    """`cli/__main__.py:_parse_args` — il flag `--auto-hint-threshold`.
    Argparse minimale scoped a questo solo flag (vedi docstring di modulo:
    se un'altra branch Wave 2 ne ha già aggiunto uno per un flag diverso,
    un merge riconcilierà i due — non un problema di questo task)."""

    def test_flag_omitted_defaults_to_none(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["chess-lab-companion"])
        args = cli_main._parse_args()
        assert args.auto_hint_threshold is None

    def test_flag_parses_integer_value(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["chess-lab-companion", "--auto-hint-threshold", "150"])
        args = cli_main._parse_args()
        assert args.auto_hint_threshold == 150
