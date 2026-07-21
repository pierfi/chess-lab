"""Client HTTP verso il backend Chess Lab esistente — il lato "mirroring" (non
di consiglio) dell'architettura hybrid della companion mode (design doc §4).

Wrappa un ``httpx.Client`` iniettabile: in produzione punta a
``http://localhost:8765`` (backend reale via uvicorn), nei test può puntare
a un ``httpx.ASGITransport(app=app)`` in-process contro la vera app FastAPI
di ``backend.main`` — stessa tecnica di ``TestClient``, ma via ``httpx``
diretto perché la CLI usa già ``httpx`` come dipendenza reale (non solo nei
test).

Solo gli endpoint companion + threats servono a questo task (Wave 1, design
doc §8) — niente ``/pgn``/``/analyze`` qui, sono follow-up separati.
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
