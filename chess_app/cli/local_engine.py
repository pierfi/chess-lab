"""Motore Stockfish LOCALE, long-lived, per il loop di consiglio a bassa
latenza della companion mode (design doc §4, "hybrid architecture" — questo è
il pezzo che la giustifica). Aperto UNA VOLTA all'avvio della sessione CLI,
riconfigurato con lo Skill Level dell'effort scelto, interrogato ad ogni
mossa con ``engine.analyse(board, Limit(depth=...), multipv=...)``. Chiuso
esplicitamente su ``/quit``.

Deroga esplicita e circoscritta al vincolo CLAUDE.md "un'istanza Stockfish
per chiamata API, mai un engine globale": quel vincolo esiste per evitare
race condition fra richieste concorrenti nel threadpool del backend FastAPI.
La CLI è un processo separato, single-user, single-thread e sequenziale —
non c'è concorrenza da cui proteggersi, quindi il vincolo semplicemente non
si applica qui (design doc §4.1). Il backend continua ad aprire/chiudere un
engine per chiamata, invariato.

Questo modulo NON tocca mai ``POST /game/{id}/hint`` — quell'endpoint apre e
chiude un secondo Stockfish ad ogni chiamata (~1-2s), inadatto al loop di
consiglio dal vivo di una REPL dove si digita in fretta. Best move/eval/
candidate per la companion mode vengono SEMPRE da qui, mai da HTTP.
"""

from __future__ import annotations

from typing import Protocol

import chess
import chess.engine

from .config import ADVICE_DEPTH, ADVICE_MULTIPV, STOCKFISH_PATH


class _AnalysingEngine(Protocol):
    """Duck-type minimo richiesto da LocalEngineAdvisor — soddisfatto sia da
    un vero ``chess.engine.SimpleEngine`` sia da uno stub nei test (niente
    spawn di un vero Stockfish per testare la logica di advice/skill)."""

    def configure(self, options: dict) -> None: ...
    def analyse(self, board: chess.Board, limit: "chess.engine.Limit", multipv: int = 1): ...
    def quit(self) -> None: ...


class LocalEngineAdvisor:
    """Wrapper sottile su un engine GIÀ APERTO (iniettato nel costruttore,
    non aperto qui dentro) — così i test passano uno stub senza spawnare un
    vero processo Stockfish. Per l'uso reale vedi ``open_local_engine()``."""

    def __init__(self, engine: _AnalysingEngine, skill_level: int | None = None) -> None:
        self._engine = engine
        if skill_level is not None:
            self._engine.configure({"Skill Level": skill_level})
        # skill_level is None (effort "Massimo") → nessuna configurazione:
        # piena forza, comportamento di default di Stockfish — coerente con
        # /hint quando hint_elo è omesso (vedi effort.py).

    def advice(
        self,
        board: chess.Board,
        multipv: int = ADVICE_MULTIPV,
        depth: int = ADVICE_DEPTH,
    ) -> dict:
        """Stessa shape di risposta di ``POST /game/{id}/hint`` (``eval_cp``
        + ``lines``), ma calcolata QUI, localmente, sulla board della CLI.
        ``depth`` resta il valore fisso di modulo — mai quello di
        ``elo_to_skill_depth()``, che è calibrato per la forza
        dell'AVVERSARIO lato backend, non per la reattività di un consiglio
        dal vivo (design doc §11.2)."""
        infos = self._engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
        if isinstance(infos, dict):
            # Alcuni motori/versioni di python-chess ritornano un dict singolo
            # invece di una lista quando multipv collassa a una sola linea.
            infos = [infos]

        lines = []
        for info in infos:
            pv = info.get("pv")
            if not pv:
                continue
            move = pv[0]
            score_pov_white = info["score"].white()
            if score_pov_white.is_mate():
                mate_in = score_pov_white.mate()
                cp_white = 10000 if mate_in > 0 else -10000
            else:
                cp_white = score_pov_white.score()
            lines.append(
                {
                    "move_uci": move.uci(),
                    "move_san": board.san(move),  # SAN sulla posizione corrente, nessun push
                    "score_cp": cp_white,
                }
            )

        # Stesso riordinamento di /hint: lines[0] deve essere la migliore per
        # chi muove ORA (score bianco decrescente se muove il bianco).
        lines.sort(key=lambda line: line["score_cp"], reverse=(board.turn == chess.WHITE))

        return {
            "eval_cp": lines[0]["score_cp"] if lines else None,
            "lines": lines,
        }

    def close(self) -> None:
        self._engine.quit()


def open_local_engine(skill_level: int | None, stockfish_path: str = STOCKFISH_PATH) -> LocalEngineAdvisor:
    """Apre il vero processo Stockfish (popen_uci) — usata dalla REPL
    interattiva reale. I test costruiscono ``LocalEngineAdvisor(fake_engine,
    skill_level=...)`` direttamente, bypassando lo spawn."""
    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
    return LocalEngineAdvisor(engine, skill_level=skill_level)
