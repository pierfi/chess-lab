"""Client HTTP verso il backend Chess Lab esistente — il lato "mirroring" (non
di consiglio) dell'architettura hybrid della companion mode (design doc §4).

Wrappa un ``httpx.Client`` iniettabile: in produzione punta a
``http://localhost:8765`` (backend reale via uvicorn), nei test può puntare
a un ``httpx.ASGITransport(app=app)`` in-process contro la vera app FastAPI
di ``backend.main`` — stessa tecnica di ``TestClient``, ma via ``httpx``
diretto perché la CLI usa già ``httpx`` come dipendenza reale (non solo nei
test).

Wave 1 Task 2 aggiungeva solo gli endpoint companion + threats. Wave 1 Task 3
aggiunge ``analyze`` (``/pgn`` non serve un metodo suo: il PGN è già nel campo
``pgn`` di ogni risposta di stato companion già gestita qui, vedi ``session.py``).
"""

from __future__ import annotations

import httpx

from .config import BASE_URL


class BackendError(Exception):
    """Errore applicativo del backend (400/404/...) — messaggio già pronto
    per essere mostrato all'utente così com'è (stesso testo di ``detail``)."""


class BackendUnavailable(Exception):
    """Il backend non è raggiungibile (connessione rifiutata, rete assente,
    timeout). La CLI degrada a modalità "solo consigli" quando questo
    succede in fase di avvio sessione (design doc §4)."""


class BackendClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    # -- Companion mode (docs/cli-companion-mode-design.md §2.2) ------------

    def new_companion_game(
        self, player_color: str, effort_elo: int, start_fen: str | None = None
    ) -> dict:
        return self._post(
            "/game/companion/new",
            {"player_color": player_color, "effort_elo": effort_elo, "start_fen": start_fen},
        )

    def companion_move(self, game_id: str, move: str, side: str | None = None) -> dict:
        return self._post(
            f"/game/{game_id}/companion/move",
            {"move": move, "side": side},
        )

    def companion_undo(self, game_id: str) -> dict:
        return self._post(f"/game/{game_id}/companion/undo", {})

    # -- Consigli letti dal backend (solo /threats — best move/eval vengono
    # dal motore locale, MAI da qui, vedi local_engine.py) -----------------

    def threats(self, game_id: str) -> dict:
        return self._get(f"/game/{game_id}/threats")

    # -- Resume di una sessione interrotta (Wave 2, design doc §11.6) -------
    # Riusa GET /game/{id} — LO STESSO endpoint del frontend web (CLAUDE.md
    # "Risposta tipo board_to_state"), che gestisce già lato server il path
    # cache-miss/reload-da-DB (_get_game()/_load_game_from_db() in
    # backend/main.py). Nessuna logica di ricostruzione duplicata qui: solo
    # l'HTTP GET, come ogni altro metodo di questa classe.

    def get_game(self, game_id: str) -> dict:
        return self._get(f"/game/{game_id}")

    # -- PGN/analisi (Wave 1 Task 3, design doc §5) — riuso puro, zero
    # endpoint nuovi. Nota: /game/analyze non ha {id} nel path, il game_id
    # va nel body (vedi AnalyzeRequest in backend/main.py). -----------------

    def analyze(self, game_id: str) -> dict:
        return self._post("/game/analyze", {"game_id": game_id})

    # -- Interno --------------------------------------------------------

    def _post(self, path: str, json_body: dict) -> dict:
        try:
            response = self._client.post(path, json=json_body)
        except httpx.TransportError as exc:
            raise BackendUnavailable(str(exc)) from exc
        return self._handle(response)

    def _get(self, path: str) -> dict:
        try:
            response = self._client.get(path)
        except httpx.TransportError as exc:
            raise BackendUnavailable(str(exc)) from exc
        return self._handle(response)

    @staticmethod
    def _handle(response: httpx.Response) -> dict:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise BackendError(detail)
        return response.json()
