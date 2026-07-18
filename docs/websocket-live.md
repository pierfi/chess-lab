# Chess Lab — WebSocket: aggiornamenti live & multi-tab (spec di design)

Data analisi: 18 luglio 2026 (Opus 4.8, branch `feature/websocket-live`)

Spec autoritativa della feature "aggiornamenti live, supporto multi-tab" di
Fase 6. Descrive il canale WebSocket di **notifica di cambio stato**, il ponte
thread→event-loop necessario a coesistere con gli endpoint sync-in-threadpool
esistenti, la forma esatta dei messaggi e il client frontend minimale.

---

## Problema

Oggi il frontend legge lo stato partita solo via `fetch()` on-demand (dopo una
propria mossa, all'apertura di una vista). Non esiste alcun canale push: se la
**stessa** `game_id` è aperta in due tab/viste, una mossa fatta in una tab non
si riflette nell'altra finché quest'ultima non rifetcha manualmente.

Obiettivo (dalla roadmap CLAUDE.md, Fase 6): *"WebSocket: aggiornamenti live,
supporto multi-tab"*. Scopo preciso e volutamente stretto: quando lo stato
osservabile di una partita cambia (mossa del player, risposta dell'engine,
game-over, cancellazione), ogni tab che ha quella `game_id` aperta riceve un
segnale *"qualcosa è cambiato"* e **rifetcha via REST** per ri-renderizzare.

### Cosa NON è (scope negativo, esplicito)

- **Non** è un pub/sub generico né un canale di broadcast globale.
- **Non** trasporta lo stato completo della partita sul filo. Il socket è un
  puro segnale di invalidazione; la **fonte di verità resta REST** (`GET
  /game/{id}`). Niente duplicazione dello stato di gioco sul canale WS. Il
  messaggio porta solo l'identità della partita e un numero di ply per il
  dedup (vedi sotto) — abbastanza per decidere *se* rifetchare, non i dati.
- **Non** tocca in alcun modo il ciclo di vita dell'engine Stockfish. Il layer
  WS è solo notifica di stato; nessun engine globale, nessun `with` engine qui
  (vincolo ferreo CLAUDE.md).
- **Non** aggiunge un time-control/clock (voce separata di Fase 6).

### Vincoli (da CLAUDE.md)

- Endpoint REST esistenti: contratto e comportamento **invariati** per i chiamanti
  attuali. Il WS è additivo.
- Nessun engine Stockfish globale/condiviso: un processo per chiamata API resta
  la regola; il WS non ne crea nessuno.
- Frontend single-file `index.html`, zero build step, nessuna dip npm: si usa la
  `WebSocket` nativa del browser, nessuna libreria.
- Commenti architetturali in italiano, inglese per il tecnico inline; pochi
  commenti.

---

## Il nodo tecnico: worker thread → event loop

Gli endpoint FastAPI dell'app sono `def` **sincroni**: FastAPI li esegue in un
**worker thread** del threadpool (anyio `to_thread`). Le connessioni WebSocket,
invece, vivono nell'**event loop asyncio**. Un worker thread **non può**:

- chiamare `await websocket.send_json(...)` (non è async);
- fare `asyncio.get_running_loop()` (nel worker thread non c'è loop in esecuzione
  → `RuntimeError`);
- toccare direttamente un oggetto WebSocket o una `asyncio.Queue` — le primitive
  asyncio non sono thread-safe.

Un approccio naive (prendere il loop e chiamare la coroutine di `send` da un
altro thread, oppure `loop.create_task` dal worker thread) **corrompe lo stato
asyncio** o non ha effetto: `create_task`/`Queue.put_nowait` vanno invocati
**sul** thread del loop, non da fuori.

### Meccanismo scelto: `call_soon_threadsafe` + coda per-connessione + task pump

L'unica API asyncio pensata per il ponte cross-thread è la famiglia
`loop.call_soon_threadsafe(...)` / `asyncio.run_coroutine_threadsafe(...)`.
Scegliamo `call_soon_threadsafe` con una **coda per connessione** drenata da un
**task "pump" dedicato**:

1. Il manager cattura il loop (`asyncio.get_running_loop()`) **pigramente alla
   prima connessione WS** — l'handler `@app.websocket` gira sul loop, quindi lì
   il loop c'è. (Non basta catturarlo nel `lifespan`: i test usano
   `TestClient(app)` **senza** `with`, quindi il lifespan non parte — vedi
   `conftest.py`. La cattura lazy alla connessione è robusta in entrambi i casi;
   e se nessuno è connesso, non c'è nulla da notificare.)
2. Ogni handler WS registra una `asyncio.Queue` nel manager, sotto un
   `threading.Lock` (il dict `game_id → set[queue]` è letto/mutato sia dal loop
   che dai worker thread).
3. `notify(game_id, msg)` — chiamabile **da un worker thread sync** — legge le
   code sotto lock e per ciascuna fa
   `loop.call_soon_threadsafe(queue.put_nowait, msg)`. Non fa `await`, non tocca
   mai il socket, non blocca la risposta dell'endpoint.
4. Il task pump di ogni handler fa `msg = await queue.get()` →
   `await websocket.send_json(msg)`. **Solo il pump** invia sul socket: nessuna
   send concorrente sulla stessa connessione (una race che `run_coroutine_threadsafe`
   diretto verso `send` non escluderebbe).

Perché `call_soon_threadsafe` e non `run_coroutine_threadsafe`: quest'ultimo
ritorna un `concurrent.futures.Future` che andrebbe gestito/atteso per non
perdere eccezioni, e invoglierebbe a bloccare il worker thread sulla send. La
coda + pump disaccoppia del tutto (fire-and-forget dal lato sync), serializza le
send per-connessione ed è naturalmente resiliente al backpressure.

### Interazione con la caveat di concorrenza già nota (Fase 2)

CLAUDE.md nota già che `/hint` e `/game/move`, entrambi sync-in-threadpool,
possono sovrapporsi sullo stesso `games[game_id]["board"]`. Il layer WS **non
peggiora** questo: `notify` viene chiamato **dopo** che `make_move` ha finito di
mutare la board e costruito lo stato, e porta solo `game_id`/`ply` (dati già
consolidati), non legge la board in modo concorrente. Il valore di `ply`
notificato è `len(game["move_objects"])` calcolato nello stesso thread subito
prima del return.

---

## Forma dei messaggi (server → client)

Un solo tipo di evento "stato cambiato" più uno di cancellazione. JSON:

```json
{ "type": "state", "game_id": "6f0610a7", "ply": 12, "is_game_over": false }
```

```json
{ "type": "deleted", "game_id": "6f0610a7" }
```

- `ply` = numero totale di semimosse dopo il cambiamento. Serve al client per il
  **dedup**: la tab che ha *fatto* la mossa ha già lo stato aggiornato (la
  risposta REST di `/game/move`), riceve comunque la propria notifica, e la
  scarta se `ply <= plies_già_applicati`. Le altre tab hanno `ply` minore →
  rifetchano. Niente client-id, niente eco-suppression lato server.
- Il client **non invia** messaggi applicativi: il canale è unidirezionale
  (server→client). L'handler legge dal socket solo per rilevare la
  disconnessione.

### Punti di notifica (dove `games[game_id]` cambia in modo osservabile)

Censiti tutti i siti dove lo stato di una partita **già esistente** muta:

| Sito | Notifica | Note |
|------|----------|------|
| `POST /game/move` (ramo game-over dopo mossa player) | `state` | ply = mosse totali |
| `POST /game/move` (ramo dopo mossa engine) | `state` | copre mossa player + risposta engine + eventuale game-over in un colpo |
| `DELETE /game/{id}` | `deleted` | dopo eviction dalla cache |
| `POST /game/new`, `POST /training/endgames/{id}/start`, `POST /games/import` | — | creano una `game_id` **nuova**: nessun subscriber possibile ancora → notify sarebbe un no-op. Non instrumentati (diff focalizzato). |
| `POST /game/analyze` | — | non muta la board/lo stato di gioco; aggiunge solo righe di analisi. Fuori scope del segnale "stato partita cambiato". |

`/game/move` esegue mossa-player + risposta-engine in **una** chiamata sync: gli
stati intermedi non sono nemmeno osservabili via REST, quindi **una** notifica a
fine chiamata è corretta e sufficiente (non due).

---

## Endpoint

```
WS /ws/game/{game_id}
```

- `accept()`, registra una coda, avvia il task pump, poi resta in `receive` per
  rilevare la disconnessione; in `finally` cancella il pump e deregistra la coda.
- Nessuna validazione di esistenza della `game_id`: il socket è un canale di
  notifica, non un accesso ai dati. Una `game_id` inesistente semplicemente non
  riceverà mai nulla. (Evita anche una query DB per connessione.)

---

## Frontend (client `WebSocket` nativo)

- `WS_API` derivata da `API` (`http→ws`, `https→wss`).
- `connectGameSocket(gameId)`: chiude l'eventuale socket precedente e ne apre uno
  nuovo su `/ws/game/{gameId}`. Chiamata quando una partita diventa attiva nella
  vista Gioca (nuova partita, drill di finali). Reconnessione best-effort con
  piccolo backoff se il socket cade mentre la partita è ancora aperta.
- `onmessage`:
  - `type: "state"` → se `game_id` combacia con `state.gameId` e
    `msg.ply > state.moveHistory.length` (dedup: ignora l'eco della propria
    mossa e i messaggi stantii) → `GET /game/{id}` → `updateState(...)`.
    updateState è già il punto unico di re-render (board, move list, hint,
    threats), quindi il refetch riusa tutta la pipeline esistente.
  - `type: "deleted"` → se combacia con la partita corrente, stato informativo.
- Il socket è **solo** un segnale: i dati arrivano sempre da REST. Se il WS non
  si connette (backend vecchio, proxy che non fa upgrade), l'app funziona
  esattamente come prima — degradazione pulita, nessuna regressione.

---

## Verifica (eseguita — 18 luglio 2026)

- **Backend, pytest** (`TestWebSocketLive`, 5 test in `tests/test_api.py`): con il
  supporto WebSocket di `TestClient` (`client.websocket_connect(...)` +
  `ws.receive_json()`) — mossa → notifica `state`; caso **multi-tab** (due
  connessioni indipendenti sulla stessa `game_id`, una mossa via REST, entrambe
  ricevono la notifica); `deleted`; isolamento per `game_id` (A non riceve gli
  eventi di B); connessione a `game_id` inesistente accettata. **Suite completa
  106 → 111 test, tutti verdi.**
- **Backend, verifica live sotto uvicorn reale** (`websockets` raw client, non
  `TestClient`): il `TestClient` esegue i WebSocket in modo sincrono e potrebbe
  mascherare problemi del ponte thread→loop. Con un uvicorn reale (threadpool +
  event loop separati) due socket raw sulla stessa `game_id` ricevono entrambe
  la notifica di una mossa fatta via HTTP **da un thread separato** (il caso che
  esercita davvero `call_soon_threadsafe` dal worker thread), più i casi
  `deleted` e isolamento per `game_id`. Tutto passa: il ponte è corretto sotto
  runtime reale, non solo nel test sincrono.
- **Frontend**: nessun browser nel sandbox; jsdom **non** implementa `WebSocket`.
  Verificato quindi con (1) `node --check` sull'intero blocco `<script>` e (2)
  un harness jsdom che carica il vero `index.html` iniettando un `WebSocket`
  mock e un `fetch` stub: conferma che `startGame`/drill aprono la socket
  sull'URL giusto, che una notifica `state` di un'altra tab (`ply` > locale)
  scatena il refetch `GET /game/{id}`, che l'**eco** della propria mossa è
  scartato (sia via flag `thinking`, sia via `ply <= moveHistory.length`), che
  le notifiche per una `game_id` diversa sono ignorate, che `deleted` non manda
  in crash, e che una nuova partita chiude la socket precedente e ne apre una
  nuova. Non si sovradichiara un test end-to-end del browser (coerente con la
  filosofia di test di CLAUDE.md: "se non puoi testare la UI, dillo").
