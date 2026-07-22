"""Stato e logica della sessione companion, indipendenti da ``input()``/
``print()`` — testabili senza un vero terminale (vedi
``chess_app/tests/test_cli.py``). La REPL interattiva (``repl.py``) è un
wrapper sottile su ``CompanionSession``.

Mantiene una ``chess.Board`` locale (alimenta il motore di consiglio e serve
da fallback in modalità degradata) SEMPRE risincronizzata dal FEN che
ritorna il backend dopo ogni chiamata riuscita — non applichiamo mai la
mossa localmente "in parallelo" a quella del backend: si ricostruisce sempre
dal FEN autoritativo, così non può esserci drift (design doc §4)."""

from __future__ import annotations

import chess

from .backend_client import BackendClient, BackendError, BackendUnavailable
from .config import _parse_companion_move
from .local_engine import LocalEngineAdvisor


def turn_prompt_label(board: chess.Board, player_color: str) -> str:
    """"hai giocato" se tocca al player riportare la mossa che ha appena
    fatto sul sito esterno, "l'avversario ha giocato" altrimenti.

    Derivazione: ``board.turn`` è il lato che deve muovere ORA sulla
    posizione tracciata — cioè il lato la cui mossa VA ANCORA riportata.
    Confrontato con ``player_color`` dice di chi è quella mossa."""
    turn_color = "white" if board.turn == chess.WHITE else "black"
    return "hai giocato" if turn_color == player_color else "l'avversario ha giocato"


def label_threats(threats_response: dict, player_color: str) -> dict:
    """Etichetta la risposta di ``GET /threats`` come "tuoi" o "dell'avversario"
    confrontando ``side`` con ``player_color`` — design doc §3.1: l'intera
    idea "segnala anche i pezzi appesi dell'avversario" ricade GRATIS da
    questa sola etichetta, nessuna logica di rilevamento nuova."""
    side = threats_response["side"]
    label = "tuoi pezzi in presa" if side == player_color else "pezzi in presa dell'avversario"
    return {"label": label, "side": side, "in_presa": threats_response["in_presa"]}


class CompanionSession:
    def __init__(self, backend: BackendClient, engine_advisor: LocalEngineAdvisor) -> None:
        self.backend = backend
        self.engine_advisor = engine_advisor
        self.game_id: str | None = None
        self.player_color: str = "white"
        self.board: chess.Board = chess.Board()
        # True se /game/companion/new non ha potuto essere chiamato (backend
        # irraggiungibile) — degrado a "consigli sì, PGN/analisi/persistenza
        # no" (design doc §4), mai un crash.
        self.degraded = False
        # Ultimo dict di stato completo ritornato da start()/register_move()/
        # undo() (in modalità non degradata) — non solo la board. Serve a
        # /pgn (campo "pgn") e alla rilevazione di game-over (campo
        # "is_game_over") SENZA un round-trip HTTP dedicato: ogni risposta di
        # stato companion li porta già (Task 3, design doc §5). Resta `None`
        # per tutta la sessione in modalità degradata (nessuna risposta di
        # stato server-side esiste in quel caso).
        self.last_state: dict | None = None

    def start(self, player_color: str, effort_elo: int, start_fen: str | None = None) -> dict:
        self.player_color = player_color
        try:
            state = self.backend.new_companion_game(player_color, effort_elo, start_fen)
        except BackendUnavailable:
            self.degraded = True
            self.game_id = None
            self.board = chess.Board(start_fen) if start_fen else chess.Board()
            self.last_state = None
            return {"degraded": True}

        self.degraded = False
        self.game_id = state["game_id"]
        self.board = chess.Board(state["fen"])
        self.last_state = state
        return state

    def turn_prompt(self) -> str:
        return turn_prompt_label(self.board, self.player_color)

    def register_move(self, move_text: str) -> dict:
        """Registra UNA mossa riportata dall'esterno (di chiunque sia il
        turno — mai forzata a coincidere col consiglio, design doc §3.2).

        Modalità normale: passa dal backend (fonte di verità, 400 su mossa
        illegale/ambigua — il messaggio torna così com'è per essere
        ririportato all'utente). Modalità degradata: valida localmente con
        lo stesso identico parsing SAN/UCI del backend (``_parse_companion_move``,
        riusato — non duplicato), perché non c'è nessun backend da
        interpellare."""
        turn_color = "white" if self.board.turn == chess.WHITE else "black"

        if self.degraded:
            move = _parse_companion_move(self.board, move_text)
            if move is None:
                return {"ok": False, "error": "Mossa illegale o non riconosciuta, ribattila."}
            self.board.push(move)
            return {"ok": True, "state": None}

        try:
            state = self.backend.companion_move(self.game_id, move_text, side=turn_color)
        except BackendError as exc:
            return {"ok": False, "error": str(exc)}

        self.board = chess.Board(state["fen"])
        self.last_state = state
        return {"ok": True, "state": state}

    def undo(self) -> dict:
        """Takeback: mis-typing dal vivo è frequente (design doc §2.2)."""
        if self.degraded:
            if not self.board.move_stack:
                return {"ok": False, "error": "Nessuna mossa da annullare."}
            self.board.pop()
            return {"ok": True, "state": None}

        try:
            state = self.backend.companion_undo(self.game_id)
        except BackendError as exc:
            return {"ok": False, "error": str(exc)}

        self.board = chess.Board(state["fen"])
        self.last_state = state
        return {"ok": True, "state": state}

    def advice(self) -> dict:
        """Consiglio locale — best move/eval/candidate. MAI un round-trip
        HTTP: è calcolato dal motore Stockfish long-lived della CLI stessa
        (design doc §4), disponibile anche in modalità degradata."""
        return self.engine_advisor.advice(self.board)

    def threats(self) -> dict | None:
        """`None` in modalità degradata o senza game_id: /threats è
        server-side (pura python-chess, deliberatamente non duplicata qui —
        vedi design doc §1.1), quindi senza un game_id tracciato non c'è
        nulla da interrogare."""
        if self.degraded or self.game_id is None:
            return None
        try:
            raw = self.backend.threats(self.game_id)
        except BackendError:
            return None
        return label_threats(raw, self.player_color)

    def write_pgn(self, path: str) -> dict:
        """Scrive su file il PGN dell'ULTIMO stato tracciato (design doc §5):
        `_build_pgn` gira lato backend, il campo `pgn` è già in ogni risposta
        di stato companion — nessuna chiamata HTTP aggiuntiva qui, puro I/O
        locale. Non disponibile in modalità degradata: senza un record
        server-side non esiste alcun PGN da scrivere (design doc §4,
        "consigli sì, PGN/analisi/persistenza no")."""
        if self.degraded or self.last_state is None:
            return {
                "ok": False,
                "error": (
                    "PGN non disponibile in modalità degradata "
                    "(nessuna sessione registrata sul backend)."
                ),
            }
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.last_state["pgn"])
        except OSError as exc:
            return {"ok": False, "error": f"Impossibile scrivere il file '{path}': {exc}"}
        return {"ok": True, "path": path}

    def analyze(self) -> dict:
        """Chiama ``POST /game/analyze`` sul ``game_id`` tracciato — riuso
        puro (design doc §5), nessun endpoint nuovo. Il backend stesso
        risponde 400 se non è ancora stata registrata alcuna mossa
        (``move_objects`` vuoto): quel messaggio torna così com'è in
        ``error``. Non disponibile in modalità degradata: nessun ``game_id``
        lato server da analizzare."""
        if self.degraded or self.game_id is None:
            return {
                "ok": False,
                "error": (
                    "Analisi non disponibile in modalità degradata "
                    "(nessuna partita registrata sul backend)."
                ),
            }
        try:
            result = self.backend.analyze(self.game_id)
        except BackendError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "result": result}

    def is_game_over(self) -> bool:
        """Riusa il segnale di game-over già confermato dal backend
        (``last_state["is_game_over"]``), MAI ricalcolato lato client —
        stessa filosofia del FEN sempre risincronizzato dalla risposta
        autoritativa. In modalità degradata non esiste nessuna risposta di
        stato server-side da controllare: l'unica fonte disponibile è la
        board locale (`python-chess` puro, nessuna chiamata al backend)."""
        if self.last_state is not None:
            return bool(self.last_state.get("is_game_over"))
        return self.board.is_game_over()

    def move_count(self) -> int:
        """Mosse registrate finora, per il riepilogo di fine partita — dal
        campo ``move_history`` dell'ultimo stato quando disponibile,
        altrimenti dalla ``move_stack`` della board locale (degrado)."""
        if self.last_state is not None:
            return len(self.last_state["move_history"])
        return len(self.board.move_stack)

    def move_history_san(self) -> list[str]:
        """SAN della cronologia mosse, per la lista mosse stilizzata (Task 4,
        ``ui.render_move_list``). In modalità non degradata è già nel campo
        ``move_history_san`` dell'ultimo stato (calcolato dal backend); in
        degrado si ricostruisce localmente rigiocando dalla posizione
        iniziale (``board.root()``) — pura ``python-chess``, nessuna
        chiamata HTTP. Scelta di derivarla sempre da zero invece che tenere
        un accumulatore separato: `self.board` è già la fonte di verità
        risincronizzata ad ogni mossa (vedi docstring di modulo), quindi non
        c'è alcun rischio di drift fra le due rappresentazioni."""
        if self.last_state is not None:
            return list(self.last_state["move_history_san"])
        replay = self.board.root()
        sans = []
        for move in self.board.move_stack:
            sans.append(replay.san(move))
            replay.push(move)
        return sans

    def close(self) -> None:
        self.engine_advisor.close()
        self.backend.close()
