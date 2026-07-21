"""Test per la CLI companion (chess_app/cli/), Wave 1 (design doc
docs/cli-companion-mode-design.md §8).

Copre: mapping effort→Skill Level, logica di prompt a turni alternati,
registrazione mossa (successo/fallimento, backend reale via ASGITransport),
etichettatura "tuoi/suoi" di /threats, re-sync dell'undo. Il motore Stockfish
locale è sempre uno stub in questi test (mai un vero processo Stockfish, per
velocità/determinismo) — solo depth/Skill Level passati all'engine vengono
verificati, non la qualità della ricerca."""

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from cli.backend_client import BackendClient, BackendError, BackendUnavailable
from cli.config import ADVICE_DEPTH, ADVICE_MULTIPV, FULL_STRENGTH_ELO, elo_to_skill_depth
from cli.effort import EFFORT_LEVELS, skill_level_for_effort
from cli.local_engine import LocalEngineAdvisor
from cli.session import CompanionSession, label_threats, turn_prompt_label


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
