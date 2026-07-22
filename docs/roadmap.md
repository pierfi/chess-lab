# Chess Lab — Roadmap dettagliata (cronologia di fase)

Questo documento è il "changelog narrativo" del progetto: per ogni fase della roadmap contiene
l'obiettivo esteso, le tabelle settimana-per-settimana (ore stimate, modello suggerito), le note
`Nota (...)`, le sotto-sezioni "Dettagli implementazione non ovvi", la narrativa di verifica/testing
e la timeline riassuntiva a fine documento.

**Non è documentazione di stato corrente** — per quello vedi `CLAUDE.md` (architettura, contratti
endpoint attuali, schema DB, convenzioni frontend, vincoli) e i singoli documenti di design in
`docs/*.md` (`training-mode.md`, `growth-analytics.md`, `theory-lessons-design.md`,
`websocket-live.md`, `cli-companion-mode-design.md`, ecc.), che restano la fonte autoritativa per
"come funziona oggi". Questo file esiste solo perché la narrazione di **come ci si è arrivati** (una
fase alla volta) è utile per chi vuole il contesto storico, ma è puro costo di token se caricata in
ogni sessione — da cui lo split. `CLAUDE.md` → sezione "Roadmap fasi" contiene la tabella compatta
con un puntatore `#fase-N` a ciascuna sezione qui sotto.

---

## Roadmap fasi

> **Assunzione:** 3–5 ore/settimana, supervisione attiva su ogni step con Claude Code.
> Le stime includono tempo di review, piccoli fix manuali e test.
> La data di riferimento è **aprile 2026**.

---

<a id="mvp"></a>
### ✅ MVP API-only — completato 16 aprile 2026

Backend funzionante: partita completa contro Stockfish + analisi via curl/HTTP.

- [x] Fix SAN in `analyze_game()`
- [x] Gestione tutti i game-over (stalemate, 50 mosse, ripetizione, materiale insufficiente)
- [x] Gestione promozione pedone in `POST /game/move`
- [x] Test pytest end-to-end (13 test)

---

<a id="fase-1"></a>
### ✅ Fase 1 — Core engine & analisi — completata 16 aprile 2026

Partita completa contro Stockfish + analisi post-partita. Backend e frontend stabili, test presenti.

**Backend:**
- [x] `POST /game/new` — crea partita, configura ELO, engine gioca primo se player è nero
- [x] `POST /game/move` — valida mossa, risposta Stockfish, promozione
- [x] `POST /game/analyze` — analisi per-mossa con classificazione e SAN corretta
- [x] `GET /game/{id}` — stato + PGN
- [x] `is_check` e `move_history_san` nella risposta API
- [x] Game-over completi (checkmate, stalemate, 50 mosse, ripetizione, materiale insufficiente)

**Frontend:**
- [x] Board interattiva con coordinate (a-h, 1-8)
- [x] Hint mosse legali (dot + anelli cattura)
- [x] Pezzi neri distinguibili dai bianchi
- [x] Move list in notazione SAN
- [x] Deseleziona cliccando stessa casella / cambia pezzo cliccando altro pezzo proprio
- [x] Highlight scacco sul re (`is_check` dal backend)
- [x] Suoni mosse (move, capture, check, game over) via Web Audio API
- [x] Conferma prima di sovrascrivere partita in corso
- [x] Analisi post-partita con accuracy %, badge, centipawn loss
- [x] Promozione pedone con pezzi del colore corretto
- [x] En passant in `generateMoveCandidates`
- [x] Slider ELO, scelta colore

---

<a id="fase-2"></a>
### ✅ Fase 2 — Assisted Play & Lichess UI — completata 7 luglio 2026

Partita "assistita" — un secondo Stockfish (analysis engine, separato da quello che gioca) mostra in tempo reale la mossa migliore, l'eval e le mosse candidate, esattamente come la scacchiera di analisi Lichess dopo una partita. Coach non-AI: nessuna dipendenza esterna, nessuna chiave API. Restyling UI in stile Lichess (screenshot di riferimento forniti dall'utente il 7 luglio 2026), rifinito su feedback diretto dell'utente.

- [x] `POST /game/{id}/hint` — analysis engine separato (MultiPV), best move + top N mosse candidate + eval, senza toccare lo stato partita (19/19 test passano)
- [x] Toggle "Assisted Mode" per-partita, nessun impatto sul flusso mossa-engine esistente
- [x] Frontend: overlay SVG per frecce sulla board (mappatura casella→coordinate coerente con la rotazione per il nero, `sqToXY()` inverso di `sqName()`) + eval bar verticale live con anti-race (`hintSeq`)
- [x] Restyling UI stile Lichess: modal impostazioni partita, tema chiaro, toolbar compatta, coordinate dentro le caselle di bordo

**Nota (post-completamento):** l'11 luglio 2026 è stata aggiunta, fuori roadmap, la **forza regolabile dell'hint engine** — `hint_elo` opzionale su `POST /game/{id}/hint` (stessa scala ELO dell'avversario, riusa `elo_to_skill_depth()` per il solo Skill Level) + selettore frontend visibile solo in modalità assistita. Default invariato: campo omesso = piena forza. Approfondisce Fase 2 senza modificarne il perimetro. Vedi [`docs/improvements.md`](docs/improvements.md).

Note tecniche:
- L'engine "assist" è un'istanza separata da quella che gioca contro il player (coerente con il vincolo "un'istanza Stockfish per chiamata API, nessun engine globale").
- MultiPV per ottenere le top N mosse candidate, non solo la migliore.
- La mossa suggerita non viene mai giocata automaticamente: resta una sovrapposizione visiva, l'utente sceglie sempre la mossa.
- Nessuna IA/LLM coinvolta in questa fase — è puro output Stockfish. Il coach AI-based (Claude) resta pianificato separatamente in Fase 7.
- **Hint-engine a piena forza per default**: NON eredita mai l'ELO del play-engine. Senza `hint_elo` nessuno Skill Level viene configurato (piena forza, comportamento storico). Dall'11 luglio 2026 l'utente può opzionalmente calibrare la forza dei suggerimenti con `hint_elo` (vedi nota sopra) — scelta esplicita, mai implicita.
- **Anti-latenza**: a depth 16 con MultiPV=3, `/hint` costa ~1-2s a chiamata. Va invocato on-demand (bottone/toggle esplicito) o con debounce — mai automaticamente dopo ogni mossa, per non raddoppiare l'attesa già presente per la mossa del play-engine.
- **Coerenza dati, non engine**: gli endpoint FastAPI restano sincroni (`def`) e girano nel threadpool, quindi `/hint` e `/game/move` possono sovrapporsi sullo stesso `games[game_id]["board"]`. I due processi Stockfish sono isolati e va bene, ma `/hint` può leggere un FEN che sta per essere superato da una `push()` concorrente. Impatto basso (l'hint non muta stato), da tenere presente se emergono risposte stale.

---

<a id="fase-3"></a>
### ✅ Fase 3 — Persistenza & storia — completata 11 luglio 2026
**Target: inizio-metà maggio 2026 · ~2 settimane · ~8 ore**

Obiettivo: le partite sopravvivono al riavvio del server. Storico consultabile e replay.

**Stato:** fondazione di persistenza + **tutti gli endpoint backend di storico/replay/delete/import/analisi + le statistiche aggregate (`/stats/summary`, `/stats/progress` con ELO simulato)** (branch `feature/history-analytics-api`) **e il frontend** (pagina Storico, replay con navigazione, import PGN, dashboard Crescita con grafici SVG) (branch `feature/history-growth-ui`) sono **completati**.

**Nota (analytics anticipata):** le statistiche aggregate e l'ELO simulato erano originariamente a roadmap in Fase 5 ("Statistiche personali"); sono state anticipate qui perché leggono lo stesso storico persistito e completano la vista "storia" del backend in un colpo solo (consolidamento persistenza+analytics, vedi memoria progetto). Spec di design autoritativa in [`docs/growth-analytics.md`](docs/growth-analytics.md).

**Nota:** l'**export** PGN (scaricare la partita corrente come `.pgn`) è stato anticipato l'11 luglio 2026, fuori roadmap — puro frontend, il backend genera già il PGN in ogni risposta di stato. Vedi [`docs/improvements.md`](docs/improvements.md). L'**import** PGN ha ora il backend pronto (`POST /games/import`, vedi sotto); resta solo l'import lato UI.

| Settimana | Attività | Ore stimate | Modello suggerito | Stato |
|-----------|----------|-------------|-------------------|-------|
| Sett. 5 mag | Schema SQLite + SQLAlchemy (5 tabelle) + migration Alembic iniziale | ~3 ore | Opus | ✅ fatto |
| Sett. 5 mag | Write-through cache + persistenza `games`/`moves` con think time su `/game/new` e `/game/move`; `start_fen` su `NewGameRequest` | ~2 ore | Opus | ✅ fatto |
| Sett. 5 mag | `GET /games` con paginazione, `DELETE /game/{id}` | ~1.5 ore | Sonnet | ✅ fatto |
| Sett. 12 mag | `GET /game/{id}/replay` (sequenza FEN) | ~1.5 ore | Sonnet | ✅ fatto |
| Sett. 12 mag | Persistenza risultati `/game/analyze` → `analysis_results` (colonne già presenti) | ~1 ora | Sonnet | ✅ fatto |
| Sett. 12 mag | Backend `POST /games/import` (parsing PGN esterno, non a roadmap in origine ma raggruppato qui perché stessa area di storico) | — | Sonnet | ✅ fatto |
| Sett. 12 mag | Statistiche aggregate `GET /stats/summary` + `GET /stats/progress` con ELO simulato (anticipate da Fase 5 — vedi `docs/growth-analytics.md`) | ~3 ore | Opus | ✅ fatto |
| Sett. 12 mag | Frontend: pagina storico, replay, import PGN, grafico di crescita | ~2 ore | Opus | ✅ fatto |

#### Schema DB reale (implementato)

Tutte e 5 le tabelle esistono già a schema (SQLAlchemy in `backend/db.py`, migration in `alembic/versions/`), anche se solo `games`/`moves` sono wired in questa fase: le fasi successive (analisi, puzzle, SRS) trovano le tabelle pronte e non devono scrivere migration contro uno schema in movimento. Niente `users` (singolo utente locale, nessuna auth). FK con `ON DELETE CASCADE`, enforcement SQLite attivo (`PRAGMA foreign_keys=ON`).

- **`games`** — `id` TEXT PK (`uuid4().hex[:8]`, stesso schema di prima, API/frontend ci dipendono), `player_color`, `engine_elo`, `result` NULL, `result_reason` NULL (`checkmate`/`stalemate`/`insufficient_material`/`fifty_moves`/`threefold_repetition`, da `_check_game_over()`), `start_fen` TEXT NULL (posizione di partenza custom; NULL = standard), `source` TEXT NOT NULL DEFAULT `'play'` (`play`/`endgame_drill`/`import` — solo `play` scritto ora, nessun CHECK così i valori futuri non vengono rifiutati), `pgn` TEXT NULL (snapshot denormalizzato, aggiornato ad ogni persistenza di mossa), `created_at` DATETIME reale, `finished_at` DATETIME NULL (settato a fine partita). Colonne riepilogo analisi lasciate NULL qui (le popola la fase analisi): `analyzed_at`, `player_accuracy`, `blunders`, `mistakes`, `inaccuracies`.
- **`moves`** — `id` INTEGER PK, `game_id` FK→games CASCADE (indicizzata), `ply` INT (1-based), `color` (`white`/`black`, memorizzato, non derivato), `uci`, `san`, `fen_before` TEXT (posizione **prima** del ply — rende banali replay e FEN-puzzle a valle, senza ri-simulare), `think_ms` INTEGER NULL (vedi timing sotto), `created_at` DATETIME. Unique `(game_id, ply)`.
- **`analysis_results`** (solo schema) — `id` PK, `game_id` FK CASCADE, `ply`, `classification`, `loss_cp`, `score_cp`, `best_move_uci` NULL, `is_mate_swing` BOOLEAN. Unique `(game_id, ply)`.
- **`puzzles`** (solo schema, self-generated Fase 4) — `id` PK, `game_id` FK CASCADE, `ply`, `fen`, `best_move_uci`, `source` (`blunder`/`mistake`), `created_at`. Unique `(game_id, ply)`.
- **`srs_cards`** (solo schema, SM-2 Fase 4) — `id` PK, `puzzle_id` FK→puzzles CASCADE UNIQUE, `due_at`, `interval_days`, `ease_factor` FLOAT DEFAULT 2.5, `correct_streak` INT DEFAULT 0, `last_reviewed_at` NULL, `created_at`.

#### Write-through cache

La cache in-memory `games: dict[str, dict]` resta l'**hot path** (contiene gli oggetti `chess.Board` vivi, non serializzabili). Il DB è la fonte **durevole**: le righe `moves` (UCI in ordine di ply) sono la verità da cui ricostruire.
- `_get_game(id)`: cache-hit → oggetto vivo; cache-miss → `_load_game_from_db()` rigioca gli UCI dal `start_fen` (o dalla posizione standard) in una fresh `chess.Board`, ripopola la cache, poi serve normalmente; 404 se la riga non esiste. Condiviso da tutti gli endpoint, quindi tutta l'app sopravvive a un restart, non solo `GET /game/{id}`.
- `/game/new` e `/game/move` inseriscono le righe **oltre** a mutare la cache; `games.pgn` (+ `result`/`result_reason`/`finished_at` a fine partita) è riscritto ad ogni persistenza così lo snapshot non diventa mai stale. La logica PGN è stata estratta in `_build_pgn(game)`, condivisa tra risposta API e persistenza (nessuna duplicazione); onora `start_fen`.

#### Timing — think time reale (NON un chess clock)

`moves.think_ms` cattura il tempo di riflessione reale, **misurato in scrittura**, non ricavato a posteriori:
- **Mossa player**: marker transiente non-persistito `game["last_ready_at"] = time.monotonic()`, settato alla fine di ogni risposta che ridà il turno a un lato (dopo `new_game` e dopo ogni `make_move`). All'inizio di `/game/move`: `player_think_ms = round((monotonic - last_ready_at) * 1000)`.
- **Mossa engine**: `_engine_move()` ritorna `(mossa, elapsed)` dove `elapsed` è il wall-time REALE della ricerca Stockfish, misurato **prima** del `sleep` cosmetico. Il padding `random.uniform(0.6, 1.5)` è solo UX pacing per i bassi ELO (ricerca quasi istantanea) e viene **escluso** dal dato — persistere il padding sarebbe disonesto.
- **Scartato di proposito**: il diff tra `moves.created_at` consecutivi. È fragile — si rompe con la persistenza in batch, confonde latenza rete/handler col think time, ed è inquinato dal sleep cosmetico lato engine.
- **Dopo un restart**: una partita ricostruita da cache-miss non ha `last_ready_at`, quindi la **prima** mossa post-restart registra `think_ms = NULL`. È comportamento atteso, non un bug — non lo risolviamo.
- Questa è **misurazione passiva**. NON è una feature time-control/clock/flagging: quella è la voce già a roadmap in Fase 6 ("Time control"), fuori scope qui.

#### Sessione / WAL / threading

- Gli endpoint sono `def` sincroni (girano nel threadpool FastAPI): engine creato con `connect_args={"check_same_thread": False}`, `sessionmaker`, e un context manager `session_scope()` (commit in uscita / rollback su errore / close) usato inline.
- **WAL + `foreign_keys=ON`** sono applicati **per-connessione** via event listener `connect` in `db.py` (più robusto di un singolo PRAGMA "at startup", copre ogni connessione del pool). WAL riduce la contesa di lock tra una scrittura `/game/move` e una lettura `/hint` concorrente (i due processi Stockfish sono già isolati). SQLite è single-writer: sufficiente per un utente locale, niente di più elaborato.

#### Alembic — batch mode obbligatorio per il futuro

Migration iniziale greenfield in `alembic/versions/` (`alembic upgrade head` dalla dir `chess_app/`). `alembic/env.py` prende URL e metadata da `backend.db` (unica fonte di verità; rispetta la env var `CHESS_LAB_DB`) e ha **`render_as_batch=True`** già attivo: qualsiasi migration **futura** che faccia ALTER/DROP su queste tabelle DEVE girare in batch mode perché il supporto `ALTER TABLE` di SQLite è molto limitato — configurato ora così le fasi successive non se ne devono ricordare. Comodità stand-alone: il `lifespan` dell'app chiama `Base.metadata.create_all()` (idempotente) così l'app parte anche senza lanciare Alembic a mano; per un setup Alembic-managed da zero, lanciare `alembic upgrade head` su un DB vuoto.

#### Dipendenze e file nuovi

- Nuove dep in `requirements.txt`: `sqlalchemy==2.0.51`, `alembic==1.18.5`.
- `.gitignore`: aggiunti `*.db`, `*.db-wal`, `*.db-shm` (il file SQLite è runtime, non versionato).
- File DB configurabile via env var `CHESS_LAB_DB` (default `backend/chess_lab.db`); i test puntano a un DB temporaneo isolato (`conftest.py`).
- `start_fen` esiste ora su `NewGameRequest` e sulla tabella `games`, pronto perché la fase drill-finali lo usi (`POST /training/endgames/{id}/start`); qui è validato (400 se FEN malformata) e propagato al `chess.Board` iniziale, ma nessun endpoint dedicato lo consuma ancora.

#### Endpoint storico/replay/delete/import (implementati 11 luglio 2026)

Tutti gli endpoint backend restanti della Fase 3 sono ora wired in `backend/main.py`. Dettagli non ovvi:

- **`POST /game/analyze` — persistenza additiva.** La risposta al chiamante non cambia (stesso shape di prima). In più, `_persist_analysis()` fa l'upsert di una riga `analysis_results` per ply (unique `game_id`+`ply`: ri-analizzare la stessa partita aggiorna le righe, non le duplica) e aggiorna `games.analyzed_at`/`player_accuracy`/`blunders`/`mistakes`/`inaccuracies`. **Difensivo:** se la riga `games` non esiste (partita iniettata solo in cache, come nel test `test_analyze_mate_swing_clamped`) la persistenza fa no-op invece di far fallire la FK — comportamento preesistente dell'endpoint preservato.
- **`GET /games`** — lista paginata/filtrata dal DB (non dalla cache, quindi funziona anche per partite non cache-hot). `result` (`win`/`loss`/`draw`) è **relativo a `player_color`**, non la stringa PGN grezza (`win` per il bianco ≠ `win` per il nero). `source` di default è **solo `'play'`** — i drill di finali e gli import restano fuori dallo storico partite a meno di richiederli esplicitamente. `move_count` è calcolato in blocco per l'intera pagina (una query `GROUP BY`, non N+1).
- **`GET /game/{id}/replay`** — riusa `_get_game()` (stessa gestione cache-hit/miss/404 di `GET /game/{id}`, nessuna logica duplicata) e `_build_pgn()`; i FEN intermedi vengono da `moves.fen_before` (già persistito per ply in Fase 1, apposta per questo), zero ri-simulazione della board. L'ultimo FEN è la posizione finale da `game["board"]`.
- **`DELETE /game/{id}`** — cancella la riga `games`; il cascade DB (`ON DELETE CASCADE` + `foreign_keys=ON`, vedi sopra) è stato **verificato in pratica con un test** (non assunto): `moves` e `analysis_results` spariscono davvero. Evict esplicito dalla cache in-memory (`games.pop`) così una richiesta in-flight non può resuscitare una partita appena cancellata leggendola dalla cache. 404 se la partita non esiste.
- **`POST /games/import`** — `chess.pgn.read_game()` su un `io.StringIO(pgn)`. Rigioca la mainline in una `chess.Board` fresca, persistendo una riga `moves` per ply (stesso shape del loop live, `think_ms=NULL` — nessun dato di timing reale per una partita non giocata qui). **Nessuna analisi automatica** — resta una chiamata esplicita separata a `/game/analyze`. Convenzioni scelte (nessun vero "player" locale in un import, ma `player_color`/`engine_elo` non sono nullable sullo schema Fase 1):
  - `player_color`: sempre `"white"` — convenzionale, determina solo a quale lato `/game/analyze` attribuisce blunder/mistake/accuracy se la partita importata viene poi analizzata.
  - `engine_elo`: sentinella `0` ("avversario sconosciuto/importato"), scelta invece di `NULL` per non alterare lo schema Fase 1 (colonna `NOT NULL`, niente nuova migration).
  - Validazione: `chess.pgn.read_game()` è tollerante — un testo non-PGN produce comunque un `Game` valido (senza `errors`) ma a **zero mosse**. La rilevazione di input spazzatura/vuoto passa quindi da "zero mosse nella mainline", non da `parsed.errors`.
  - La partita importata viene subito messa in cache (`games[game_id] = ...`), quindi è immediatamente giocabile/analizzabile senza dover attendere un round-trip di cache-miss sul DB.

#### Frontend: Storico + Crescita (implementato, `frontend/index.html`)

Tutto in `chess_app/frontend/index.html` (nessun file nuovo, resta single-file). Nessuna modifica al backend.

- **Navigazione a tab** (`<nav class="topnav">`): Gioca / Storico / Crescita. `showView(name)` mostra/nasconde i tre contenitori (`#view-play`, `#view-history`, `#view-growth`) via `style.display`; nessun router, nessun hash URL — coerente con "nessun framework". Entrare in Storico o Crescita ricarica sempre i dati dal backend (`loadHistory()`/`loadGrowth()`), così una partita appena giocata nella vista Gioca compare subito.
- **Renderer board condiviso**: `renderBoard()` (partita live) è stato refattorizzato per estrarre `buildBoardEl({fen, orientation, lastMove, selectedSq, legalMoves, isCheck, onSquare})`, che costruisce la griglia 8×8 pura (nessuno stato globale). Sia la partita live sia il replay chiamano questa stessa funzione — la board del replay è la stessa identica griglia, in sola lettura (`onSquare` omesso). `sqName(i)` è ora un wrapper sottile su `sqNameFor(i, orientation)`, condivisa tra i due orientamenti (quello della partita corrente e quello — `player_color` della partita replayata — dello storico).
- **Storico** (`GET /games`): lista paginata con filtri colore/esito/sorgente (select semplici, non debounced — request-per-change è già istantanea sul dataset locale). `result` resta relativo a `player_color` esattamente come il backend (`_result_predicate`/`_player_result`): la funzione JS `playerResultOf()` è la controparte client-side della stessa convenzione, non un calcolo indipendente. Cancellazione (`DELETE /game/{id}`) dietro un modal di conferma riusato dal pattern `.modal-overlay` esistente — azione distruttiva, mai un click diretto.
- **Replay** (`GET /game/{id}/replay`): `fens[idx]` con `moves[idx-1]` per l'etichetta "dopo quale mossa"; prev/next/start/end + click-to-jump sulla move-list + frecce tastiera (←/→/Home/End, attive solo a vista Storico aperta e replay in corso). Funziona identico su partite giocate e importate (stesso endpoint, nessuna branch nel frontend).
- **Import PGN** (`POST /games/import`): textarea + upload file (`FileReader.readAsText`, nessun upload multipart — il contenuto testuale finisce comunque nello stesso body JSON `{pgn}`). Dopo un import riuscito il filtro sorgente passa automaticamente a "Importate" così la partita compare subito (coerente col default backend `source=play` che altrimenti la nasconderebbe).
- **Crescita** (`GET /stats/summary` + `GET /stats/progress`): 6 stat-card headline + il blocco "ultime 10" (`recent`) + due grafici SVG inline. **Nessuna libreria di charting** (vincolo CLAUDE.md) — riusa la stessa tecnica di `buildEvalChartSvg()` già presente per l'analisi post-partita (path SVG costruito a mano, punti cliccabili con `<title>` per il tooltip nativo del browser, nessun asse doppio). Estratta in una funzione generica `buildTrendChartSvg({points, startValue, yLo, yHi, yMid, yFmt, ariaLabel, endLabel})` condivisa da ELO e accuracy — due grafici separati (non un doppio asse y, che avrebbe scale incompatibili): `buildEloChartSvg()` include un punto di partenza virtuale al seed ELO; `buildAccuracyChartSvg()` salta le partite non analizzate (`accuracy: null`) senza comprimere l'asse x — restano un "buco" alla loro posizione cronologica reale, così la spaziatura tra punti riflette il numero di partite intercorse, non il numero di partite analizzate.
- **Verifica**: nessun browser Chromium disponibile in questo sandbox (libreria di sistema `libnspr4.so` mancante, nessun `sudo`). Verificato invece con: (1) `node --check` sull'intero blocco `<script>`; (2) un harness `jsdom` che carica il vero `index.html`, stubba solo `AudioContext`/`scrollIntoView` (assenti in jsdom, presenti in ogni browser reale) e guida `showView`, `loadHistory`, `histFiltersChanged`, `openReplay`, `replayStep/Goto`, `importPgn`, `confirmDelete`, `loadGrowth` con `fetch()` reali contro un backend live popolato da partite vere giocate via API (Stockfish 400/900/1900 ELO, una lasciata in corso, una analizzata); (3) verifica diretta via `curl` di ogni endpoint chiamato dal frontend. Tutti i controlli passano contro dati reali.

---

<a id="fase-4"></a>
### ✅ Fase 4 — Allenamento mirato: errori, ripasso e finali — completata 11 luglio 2026
**Target: metà-fine maggio 2026 · ~3 settimane · ~14 ore**

Obiettivo: trasformare gli errori giocati in materiale di allenamento reale, non solo in statistiche a consuntivo. Puzzle generati dai propri blunder, ripasso a intervalli (spaced repetition), diagnosi delle debolezze per fase di gioco e tema tattico, drill di finali teorici. Dipende dalla persistenza di Fase 3 (`analysis_results`). Analisi di design completa in [`docs/training-mode.md`](docs/training-mode.md).

**Stato:** tutti gli endpoint backend (puzzle da blunder + SRS, profilo debolezze, drill di finali) sono **completati** (11 luglio 2026, branch `feature/training-backend`, 25 nuovi test — 93/93 nella suite). Il **frontend** (pannello "Allenamento": risoluzione puzzle, dashboard debolezze, selezione drill finali) è **completato** l'11 luglio 2026 sul branch `feature/training-ui` — vedi le note di implementazione frontend più sotto.

| Settimana | Attività | Ore stimate | Modello suggerito | Stato |
|-----------|----------|-------------|-------------------|-------|
| Sett. 19 mag | Schema `puzzles` + `srs_cards`; generazione puzzle da `analysis_results` (ogni mossa con `classification` blunder/mistake diventa un puzzle: FEN prima dell'errore + `best_move_uci`) | ~3 ore | Opus | ✅ fatto (schema già presente da Fase 3, nessuna migration servita) |
| Sett. 19 mag | `GET /training/puzzles/next`, `POST /training/puzzles/{id}/answer` con scheduling SM-2 semplificato | ~3 ore | Opus | ✅ fatto |
| Sett. 26 mag | `GET /training/weaknesses` — aggregazione errori per fase (apertura/mediogioco/finale) e tema tattico (fork/pin/re esposto) da `analysis_results` | ~3 ore | Opus | ✅ fatto |
| Sett. 26 mag | Drill finali teorici: `GET /training/endgames` (lista statica ~15-20 FEN canonici), `POST /training/endgames/{id}/start` (estende `POST /game/new` con `start_fen` opzionale) | ~2 ore | Sonnet | ✅ fatto |
| Sett. 2 giu | Frontend: pannello "Allenamento" — risoluzione puzzle, dashboard debolezze, selezione drill finali | ~3 ore | Opus | ✅ fatto |

Tabelle DB (già presenti a schema da Fase 3, nessuna migration nuova):
- `puzzles` — id, game_id, ply, fen, best_move_uci, source (`blunder`\|`mistake`\|`inaccuracy` — vedi nota fallback sotto), created_at
- `srs_cards` — id, puzzle_id, due_at, interval_days, ease_factor, correct_streak, last_reviewed_at

**Nota:** questi puzzle nascono dalle proprie partite (self-generated) — concettualmente distinti dalla "Modalità puzzle" di Fase 6 (dataset Lichess esterno, FEN generiche). Le due funzionalità convivono, non si sovrappongono.

#### Dettagli implementazione non ovvi (11 luglio 2026)

- **Schema già pronto, zero migration.** Le tabelle `puzzles`/`srs_cards` create in Fase 3 corrispondevano già esattamente allo schema di `docs/training-mode.md` (colonne, unique constraint, FK CASCADE) — questa fase ha solo scritto la logica applicativa, nessun `alembic revision` servito.
- **`source` esteso a `inaccuracy`.** Lo schema non ha un vero `CHECK` a runtime (solo `String(16)`), quindi il fallback esplicitamente previsto dalla spec ("pochi blunder registrati → includere anche `inaccuracy`") è stato implementato senza toccare lo schema: `puzzles.source` può valere `blunder`\|`mistake`\|`inaccuracy`.
- **`GET /training/puzzles/next` — priorità e filtro opzionale `source`.** Ordine: (1) prima carta SRS scaduta (`due_at <= now`, qualunque sia la partita di origine); (2) se nessuna è scaduta, il blunder/mistake più recente (per `games.created_at`, poi `ply`) senza già una riga `puzzles` per lo stesso `(game_id, ply)`; (3) fallback a `inaccuracy` solo se (2) non trova nulla. Il parametro opzionale `?source=` (default: nessun filtro, comportamento invariato) limita la **generazione di nuovi** puzzle a un `games.source` specifico — non filtra la coda di ripasso. Aggiunto per coerenza con `GET /games`/`/stats/*` e per isolare i test dallo storico condiviso, non richiesto dalla spec originale.
- **SM-2: la carta nasce al primo tentativo**, non alla generazione del puzzle (`SrsCard` creata dentro `POST /training/puzzles/{id}/answer`, non da `/next`) — un puzzle mai risposto non è "in coda di ripasso", come da spec. Match `move_uci` vs `best_move_uci` case-insensitive, nessuna tolleranza in centipawn (puzzle a soluzione unica).
- **`GET /training/weaknesses` — solo errori del PLAYER.** Join `analysis_results` → `moves` (stesso `game_id`+`ply`, per leggere `moves.color`) → `games`, filtrato su `moves.color == games.player_color`: un blunder dell'engine non entra nell'aggregazione. `source` di default `'play'`, stessa convenzione di `GET /games`.
  - **Fase di gioco**: `ply <= 20` → apertura; altrimenti materiale residuo (donna=9, torre=5, alfiere/cavallo=3, pedoni/re esclusi) `<= 13` → finale; il resto è mediogioco.
  - **Temi tattici**: euristiche `python-chess` **approssimate** (esplicitamente NON un motore tattico, per scelta di design) — fork = la mossa migliore porta un pezzo che attacca ≥2 pezzi avversari non-pedone e la mossa giocata no; pin = la mossa migliore crea un `is_pinned()` nuovo su un pezzo avversario che la mossa giocata non crea; re esposto = la mossa giocata riduce lo scudo pedonale del proprio re (pedoni propri nelle 2 file/ranghi davanti al re) più di quanto avrebbe fatto la mossa migliore. Solo righe `blunder`/`mistake` contribuiscono ai temi (non `inaccuracy`/`good`). La risposta include un campo `"note"` che ricorda esplicitamente la natura euristica ("temi probabili", non diagnosi certa), come richiesto dalla spec.
- **Drill di finali — fix di un bug latente in `_create_new_game`.** La vecchia logica di `/game/new` decideva la prima mossa dell'engine con `if player_color == "black"` hardcoded, assumendo sempre bianco al tratto all'inizio (vero solo per la posizione standard, mai esercitato da uno `start_fen` custom fino ad ora). Il drill "Philidor" parte col **nero** al tratto: `_create_new_game` ora deduce il turno iniziale da `board.turn` e fa aprire l'engine solo se non coincide col colore scelto dal player — generalizza il comportamento esistente (per la posizione standard è un no-op, verificato dai test Fase 3 già passanti) e lo rende corretto anche per FEN custom. `POST /training/endgames/{id}/start` riusa `_create_new_game(..., source="endgame_drill")`, nessuna duplicazione con `/game/new`.
- **16 posizioni** nel set statico (`ENDGAME_DRILLS` in `main.py`): matti elementari (KQvK, KRvK, K2RvK, due alfieri vK, alfiere+cavallo vK), K+P (opposizione vincente e patta, pedone passato lontano, trébuchet), finali di torre (Lucena, Philidor, torre vs alfiere/cavallo, pedone di torre), donna vs pedone in settima, donna vs torre. Stockfish a piena forza (già usato altrove nell'app) funge da "tablebase" didattica, coerente con la scelta di design della spec.

#### Frontend: pannello "Allenamento" (implementato 11 luglio 2026, `frontend/index.html`)

Quarta tab nella topnav (Gioca / **Allenamento** / Storico / Crescita), stesso pattern `showView()` senza router. Tutto nel singolo `index.html`, nessuna modifica al backend. Quattro sotto-sezioni:

- **Puzzle solver** (`GET /training/puzzles/next` + `POST /training/puzzles/{id}/answer`): board col renderer condiviso `buildBoardEl()`, orientata su `player_to_move`; interazione click-pezzo→click-destinazione identica alla partita live (riusa `generateMoveCandidates` e `askPromotion` per le promozioni), ma su uno stato separato `training` — la partita live nella vista Gioca non viene toccata. Dopo la risposta la board diventa read-only e la mossa migliore è evidenziata col highlight `.last-move`; feedback corretto/sbagliato + scheduling SRS (prossimo ripasso, streak). Badge sorgente riusa le classi `.badge blunder/mistake/inaccuracy` esistenti. Coda vuota gestita col messaggio del backend + rimando a drill/analisi.
  - **Anti-orfani:** rientrare nella vista NON rifetcha un puzzle ancora senza risposta — ogni `GET /puzzles/next` a coda SRS vuota *genera* un puzzle nuovo dal blunder successivo, e la carta SRS nasce solo alla prima risposta: rifetch indiscriminato orfanerebbe puzzle mai tentati.
- **Dashboard debolezze** (`GET /training/weaknesses`): barre orizzontali HTML/CSS pure (nessuna libreria, niente SVG qui — più semplice del pattern chart), una sola tinta (`--blue`) per gruppo perché ogni gruppo è una sola serie di magnitudine, larghezza relativa al massimo del proprio gruppo, valore sempre in testo accanto alla barra. Il campo `note` del backend ("temi probabili, non diagnosi certa") è mostrato testualmente sotto le barre.
- **Drill di finali** (`GET /training/endgames` + `POST /training/endgames/{id}/start`): lista statica con badge obiettivo (Vinci/Patta) e select forza avversario (default 2400 — difesa/attacco quasi-tablebase è il senso didattico del drill). Il player gioca il **lato al tratto sul FEN** del drill (è il lato che ha l'obiettivo); l'avvio ruota nella vista Gioca e riusa il flusso live esistente (`updateState`), nessuna modalità parallela. Refactor minimi a supporto: `resetPlayUi()` estratto da `startGame()` e `requestGameStart(fn)` che generalizza il modal di conferma "partita in corso" a qualsiasi azione di avvio (nuova partita o drill).
- **Verifica** (stessa tecnica di Fase 3, nessun browser disponibile): `node --check` sullo script; harness `jsdom` che carica il vero `index.html` (unica patch: API→porta di test) e guida i flussi con `fetch()` reali contro un backend isolato (DB scratch via `CHESS_LAB_DB`, porta 8766) popolato da partite vere giocate e analizzate via API — caso coda-vuota su DB fresco, risposta sbagliata e corretta via click sulle caselle, barre debolezze, avvio drill → mossa reale nella vista Gioca → modal di conferma sul secondo drill, drill Philidor col nero al tratto. 93/93 test backend invariati.
- **Lezioni di teoria** (implementata 19 luglio 2026, `GET /training/lessons` + `/lessons/{id}`, vedi [`docs/theory-lessons-design.md`](docs/theory-lessons-design.md)): strato didattico "a monte" delle altre tre sotto-sezioni — spiega un concetto prima di mettere alla prova. Riusa `buildBoardEl()` + lo **stepping stile replay** (avanti/indietro/inizio/fine, frecce tastiera, autoplay a intervallo fisso che si ferma sempre su uno step `"play"` invece di attraversarlo) per gli step `"show"`, e l'**interazione stile puzzle** (click-pezzo → `generateMoveCandidates()` → click-destinazione, `askPromotion()` per le promozioni) per gli step `"play"` — ma la validazione della mossa "play" è un **confronto UCI lato client** contro `line[idx].uci`, non una chiamata al backend: la soluzione è già nei dati fetchati, a differenza dei puzzle self-generated che devono passare dal server perché non la conoscono. Stato dedicato `lesson`, separato da `state`/`training`/`ext`. Pannello commento/intro sincronizzato con lo step corrente. Bottone "prova nel drill" quando `related_drill_id` è valorizzato (oggi solo `lucena-ponte` → drill `lucena`), riusa `requestGameStart()` esistente — nessun flusso parallelo di avvio partita. **Verifica**: harness jsdom esteso con 24 nuovi check (lista lezioni, apertura/intro, stepping show, mossa sbagliata su uno step play non avanza, mossa giusta avanza, autoplay si ferma sullo step play, completamento, bottone drill assente/presente in base a `related_drill_id`, click sul bottone avvia davvero il drill Lucena con il FEN corretto) — 102/102 check totali, 165/165 test backend invariati.

---

<a id="fase-5"></a>
### ✅ Fase 5 — Analisi avanzata — completata (anticipata) 11 luglio 2026
**Target: giugno 2026 · ~3 settimane · ~10 ore**

Obiettivo: trasformare l'app in un vero trainer con feedback quantitativo sui progressi.

**Stato:** tutte e quattro le attività di Fase 5 risultano completate, ma due di esse (statistiche personali + dashboard riepilogo) erano già state **anticipate in Fase 3** l'11 luglio 2026 — vedi la nota "analytics anticipata" nella sezione Fase 3 sopra — e non sono mai state lavoro separato in questa fase. La tabella sotto è stata corretta per riflettere questo (in precedenza le due righe restavano erroneamente segnate come "prossimo" nonostante il lavoro fosse già stato fatto altrove).

| Settimana | Attività | Ore stimate | Modello suggerito |
|-----------|----------|-------------|-------------------|
| Sett. 9 giu | ✅ Grafico eval: curva centipawn, highlight blunders, click → jump mossa — **anticipato, completato l'11 luglio 2026** su `feature/analysis-panel-v2` insieme al restyling a due colonne del pannello analisi (vedi [docs/improvements.md](docs/improvements.md)) | ~3 ore | Opus |
| Sett. 16 giu | ✅ Identificazione apertura ECO live (eco.json locale) — **completato 18 luglio 2026** su `feature/eco-openings` | ~2.5 ore | Sonnet |
| Sett. 23 giu | ✅ Statistiche personali: accuracy storica, errori frequenti, ELO simulato — **già coperta dall'anticipazione di Fase 3** (`GET /stats/summary` + `GET /stats/progress`, vedi sezione Fase 3), nessun lavoro separato qui | ~3 ore | Opus |
| Sett. 23 giu | ✅ Dashboard riepilogo (ultimi 10 match, trend accuracy) — **già coperta dall'anticipazione di Fase 3** (dashboard Crescita: 6 stat-card, blocco "ultime 10", grafici SVG ELO/accuracy), nessun lavoro separato qui | ~1.5 ore | Sonnet |

**Nota (aperture ECO, 18 luglio 2026):** dataset curato in `backend/data/eco.json` (822 righe: eco, name, uci, san), 822/822 validate programmaticamente contro `python-chess` (ogni SAN si riparsa nell'UCI atteso, nessun duplicato di chiave) — copertura più ampia della stima iniziale "~500" di roadmap. `backend/eco_book.py` espone `match_opening(move_history_uci)`: longest-prefix match puro in memoria (book caricato una volta all'import, nessuna dipendenza dal DB). Wired come campo `"opening"` (`{"eco", "name"} | null`) in `_board_to_state` — quindi su `POST /game/new`, `POST /game/move`, `GET /game/{id}`, `POST /games/import`, `POST /training/endgames/{id}/start` — e su `GET /game/{id}/replay`. Una `start_fen` custom (drill di finali) non viene mai matchata: il book è costruito sulla posizione standard, matchare una posizione arbitraria non avrebbe senso, quindi `_current_opening()` ritorna `null` a prescindere dalle mosse se `game["start_fen"]` è valorizzato. Frontend: badge ECO+nome (`#opening-display`) sopra la move-list nella vista Gioca, aggiornato ad ogni `updateState()`, nascosto quando fuori libro. 9 nuovi test pytest (`TestOpening` in `tests/test_api.py`: match a mossa singola, aggiornamento ply-per-ply, righe note — Ruy Lopez/Italiana/Siciliana —, sequenza fuori libro → null, fallback al prefisso più lungo dopo la divergenza, nessun match con `start_fen` custom) — 115/115 nella suite. Verifica frontend via lo stesso harness jsdom delle fasi precedenti (`tests/frontend_harness.mjs`), estesa con controlli sul wiring end-to-end e sul rendering del badge — 45/45 check.

---

<a id="fase-6"></a>
### 🔲 Fase 6 — UX avanzata & real-time
**Target: fine giugno / luglio 2026 · ~3 settimane · ~10 ore**

Obiettivo: funzionalità avanzate per rendere il training più vario e coinvolgente.

**Stato:** tutte e tre le attività di Fase 6 sono **completate** (18 luglio 2026): modalità puzzle esterna (branch `feature/puzzle-mode-external`), time control (branch `feature/time-control`, 19 nuovi test — 125/125 nella suite backend + harness jsdom esteso, 57/57 check), WebSocket live (branch `feature/websocket-live`).

| Settimana | Attività | Ore stimate | Modello suggerito | Stato |
|-----------|----------|-------------|-------------------|-------|
| Sett. 30 giu | Modalità puzzle: FEN custom, mossa corretta unica, feedback immediato | ~4 ore | Opus | ✅ fatto (18 luglio 2026, branch `feature/puzzle-mode-external` — vedi sotto) |
| Sett. 7 lug | Time control: clock digitale, bullet/blitz/rapid, Fischer increment | ~3 ore | Sonnet | ✅ fatto (18 luglio 2026, branch `feature/time-control` — vedi sotto) |
| Sett. 14 lug | WebSocket: aggiornamenti live, supporto multi-tab | ~3 ore | Opus | ✅ fatto (18 lug 2026, branch `feature/websocket-live` — vedi sotto) |

**Nota:** il dataset Lichess puzzles (CSV ~50 MB) richiede un import script separato e uno schema dedicato. Valutare se incluso in Fase 6 o posticipato. Puzzle da dataset esterno, distinti dai puzzle self-generated di Fase 4. → **Risolto** con un bundle statico curato (vedi sotto): niente import del CSV completo, nessuno schema di import incrementale.

#### Modalità puzzle (dataset Lichess esterno) — implementata 18 luglio 2026

Trainer tattico su posizioni **generiche** dal Lichess puzzle database — sistema DISTINTO dai puzzle self-generated di Fase 4 (`/training/puzzles`, tabelle `puzzles`/`srs_cards`), che resta intoccato: nessuna FK verso `games`, nessuna carta SRS, nessuna scrittura DB durante la risoluzione.

**Sourcing dei dati — bundle statico curato, non il CSV completo.** Il dataset ufficiale (`lichess_db_puzzle.csv.zst`, ~300 MB compressi, milioni di puzzle, licenza CC0) è sproporzionato per un'app locale single-user. `scripts/build_puzzle_bundle.py` (one-off, richiede rete + `zstandard`, NON in requirements.txt) scarica una slice iniziale del file reale via HTTP Range (~12 MB), la decomprime parzialmente, filtra per qualità (Popularity ≥ 90, NbPlays ≥ 500, RatingDeviation ≤ 100, linee ≤ 4 mosse del solutore), **valida ogni puzzle con python-chess** (FEN + legalità dell'intera linea) e campiona ~400 puzzle stratificati per fascia di rating (120 <1200, 120 1200–1599, 100 1600–1999, 60 2000+; seed fisso, build riproducibile a parità di slice). Output: `backend/data/lichess_puzzles.json` (~100 KB, versionato — provenienza e licenza in `backend/data/NOTICE.md`). A runtime l'app **non tocca mai la rete** — stesso precedente di `ENDGAME_DRILLS`. In fase di build la mossa di setup Lichess (prima mossa del campo `Moves`) viene già applicata alla FEN: nel bundle `fen` è la posizione col solutore al tratto, `initial_uci` è la mossa avversaria che l'ha generata (highlight UI) e `moves` è la sola linea di soluzione (solutore per primo, lunghezza dispari).

**Schema** — tabella `external_puzzles` (migration Alembic `c41e8d5a2f90`, `create_table` puro: batch mode non necessario, nessun ALTER): `id` TEXT PK (PuzzleId Lichess), `fen`, `initial_uci`, `moves_uci` (linea spazio-separata), `rating` INT indicizzato, `themes` (spazio-separati), `lichess_url` NULL. Seed idempotente dal bundle in `db.seed_external_puzzles()` (chiamato dal lifespan e da conftest): popola solo se la tabella è vuota — per aggiornare il bundle si rigenera il JSON e si riparte da una tabella senza righe.

**Endpoint** (in `main.py`, sezione "Fase 6"):
- `GET /puzzles/next?theme=&min_rating=&max_rating=&exclude=` — puzzle casuale (ORDER BY RANDOM(), ~400 righe: costo irrilevante) filtrabile per tema (match a parola intera su `themes`, non substring) e fascia rating; `exclude` (ultimo id mostrato) evita la ripetizione immediata, **best-effort**: se l'unico match è quello escluso viene riproposto invece di rispondere "nessun puzzle". La shape pubblica **non espone mai la soluzione** — solo `solution_moves` (numero di mosse del solutore) per il progresso UI. Nessun match → `{"puzzle_id": null, "message": ...}`.
- `GET /puzzles/themes` — temi disponibili con conteggio (aggregazione in Python), per la select del frontend.
- `POST /puzzles/{id}/answer` `{move_index, move_uci}` — validazione **stateless**: il server ricostruisce la posizione da FEN + prefisso di soluzione (nessuno stato di sessione, nessuna scrittura DB). `move_index` è 0-based sulla linea, solo indici pari (le dispari sono le risposte avversarie auto-giocate); 400 se dispari/oltre linea/UCI malformato/mossa illegale, 404 se id inesistente. **Regola Lichess**: un matto immediato alternativo alla mossa attesa è comunque corretto (`solved_by_alternate_mate`) e completa il puzzle. Mossa giusta a linea non finita → la risposta include la contromossa (`reply_uci`/`reply_san`) e `next_fen` già con entrambe applicate (il client non applica mai mosse a una FEN da solo) + `next_move_index`. Mossa sbagliata → puzzle fallito, `expected_uci` sempre presente per mostrare la soluzione del passo.

**Frontend** — quinta tab "Puzzle" (Gioca / Allenamento / **Puzzle** / Storico / Crescita), stesso pattern `showView()`, tutto nel singolo `index.html`. Stato dedicato `ext` (separato sia da `state` sia da `training`); board col renderer condiviso `buildBoardEl()` orientata su `player_to_move`, highlight della mossa avversaria di setup, interazione click-click identica alla partita live (riusa `generateMoveCandidates`/`askPromotion`). Filtri tema (select popolata da `/puzzles/themes`, etichette italiane in `EXT_THEME_LABEL`, solo temi con ≥8 puzzle) e difficoltà (4 fasce); progresso "mossa N di M" sulle linee multi-mossa, riepilogo SAN della linea giocata, punteggio di sessione (in memoria, non persistito). Fallimento → board sulla posizione dell'errore con la mossa attesa evidenziata; rientrare nella vista NON abbandona un puzzle in corso; cambiare filtro sì (non conta come tentato). Nota testuale in vista che rimanda i puzzle "dalle tue partite" alla tab Allenamento — le due funzionalità convivono senza confondersi.

**Verifica** — 17 nuovi test pytest (123/123 verdi); harness jsdom (`tests/frontend_harness.mjs`, esteso con la sezione puzzle: risoluzione dell'intera linea leggendo la soluzione dal bundle — mai dall'API — exclude, fail path, select temi) contro backend isolato.

#### WebSocket — aggiornamenti live & multi-tab (implementato 18 luglio 2026)

Canale WS di **sola notifica** di cambio stato: se la stessa `game_id` è aperta in più tab, una mossa in una tab fa rifetchare le altre via REST — non un pub/sub generico, non stato-sul-filo. Spec autoritativa: [`docs/websocket-live.md`](docs/websocket-live.md). Dettagli non ovvi:

- **Ponte thread→event-loop (il nodo tecnico).** Gli endpoint sono `def` sincroni nel threadpool, ma le connessioni WS vivono sull'event loop asyncio: un worker thread **non** può toccare il socket né una `asyncio.Queue`. Il ponte è `loop.call_soon_threadsafe` — l'unica API asyncio cross-thread. `GameConnectionManager.notify()` (chiamata dal worker sync dopo che `make_move` ha finito di mutare la board) schedula sul loop il `put_nowait` in una **coda per-connessione**, drenata da un **task "pump"** dedicato che è l'unico a fare `send_json` (nessuna send concorrente sullo stesso socket). Il loop è catturato **pigramente alla prima connessione** (`asyncio.get_running_loop()` nell'handler WS), non nel `lifespan` — i test usano `TestClient(app)` senza `with`, quindi il lifespan non parte. **Nessun engine Stockfish coinvolto**, vincolo ferreo rispettato.
- **`WS /ws/game/{game_id}`** — unidirezionale server→client, nessuna validazione di esistenza (canale di notifica, non accesso ai dati). Messaggi: `{type:"state", game_id, ply, is_game_over}` e `{type:"deleted", game_id}`. `ply` = mosse totali, per il **dedup** lato client.
- **Siti di notifica**: `POST /game/move` (una notifica a fine chiamata, copre mossa player + risposta engine + eventuale game-over) e `DELETE /game/{id}`. `/game/new`/import/drill creano una `game_id` nuova (nessun subscriber ancora) → non instrumentati. `/game/analyze` non muta la board → fuori scope. **Contratti REST esistenti invariati** (il WS è additivo).
- **Frontend** (`index.html`, single-file): `WS_API` derivata da `API` (`http→ws`). `connectGameSocket(gameId)` alla nuova partita e al drill di finali; `onmessage` `state` → refetch `GET /game/{id}` → `updateState` (pipeline di re-render esistente, la **fonte di verità resta REST**). Dedup dell'eco della propria mossa: ignora se la tab sta giocando (`state.thinking`) o se `ply <= moveHistory.length`. Riconnessione best-effort con backoff se il socket cade a partita aperta; degradazione pulita se il WS non si connette (app identica a prima).
- **Verifica**: suite pytest 106 → **111 test verdi** (5 nuovi, incl. multi-tab/deleted/isolamento via `TestClient`); verifica **live sotto uvicorn reale** con client `websockets` raw (il `TestClient` esegue i WS in modo sincrono e maschererebbe un problema del ponte thread→loop) — due socket raw ricevono la notifica di una mossa fatta da un thread separato; harness jsdom con `WebSocket` mock per la logica del client frontend (jsdom non implementa `WebSocket`). Dettagli in `docs/websocket-live.md`.

#### Time control — dettagli implementazione (18 luglio 2026)

Campo opzionale `time_control: {initial_seconds, increment_seconds} | null` su `NewGameRequest`/`POST /game/new`. `null` (default) = partita non a tempo — **no-op logico completo**: nessun clock debitato, nessuna bandierina, nessuna colonna DB valorizzata. I preset bullet/blitz/rapid sono **solo una comodità frontend** (select nel modal impostazioni: 1+0, 2+1, 3+2, 5+0, 10+0, 15+10) sopra la coppia arbitraria `initial_seconds`+`increment_seconds` che il backend accetta (15s–3h, incremento 0–60s) — il backend non conosce il concetto di "bullet"/"blitz".

- **Riuso del meccanismo di timing di Fase 3, non un secondo sistema.** Il clock si debita con lo **stesso dato** già usato per `moves.think_ms`: `last_ready_at`/`time.monotonic()` per il player, `elapsed` reale della ricerca Stockfish (misurato **prima** del `sleep` cosmetico di pacing) per l'engine — mai il padding UX. Se `last_ready_at` è assente (mossa post-restart/cache-miss, stesso caso limite già documentato in Fase 3) il clock per quella mossa **non viene né debitato né incrementato**: un bug scoperto in review (l'incremento veniva accreditato comunque, un "regalo" senza debito corrispondente) è stato corretto avvolgendo debito+incremento nello stesso `if player_think_ms is not None`, coperto dal test `test_untimed_moves_do_not_credit_free_increment_after_cache_miss`.
- **Bandierina (`result_reason: "timeout"`).** Stesso pattern dict `{"result","reason"}` di `_check_game_over()`, non un meccanismo parallelo — `_game_over_info()` fa da unione (`result_override or _check_game_over(board)`). La mossa che fa scattare la bandierina **non viene mai applicata**: il flag scatta "durante" il pensiero/la ricerca, non dopo. Simmetrico per entrambi i lati — sia sul player (controllato prima di validare la mossa) sia sull'engine (controllato dopo l'elapsed reale, prima di eseguire `board.push()`), incluso il caso limite della mossa d'apertura dell'engine quando il player è nero.
- **Persistenza.** 4 colonne nuove su `games` (`initial_seconds`, `increment_seconds`, `white_clock_ms`, `black_clock_ms`), migration Alembic in batch mode (`6bab8cd6dbe4_add_time_control_columns.py`, verificata con upgrade→downgrade→upgrade su un DB scratch). Il clock è scritto write-through ad ogni persistenza di mossa (stesso pattern di `games.pgn`). **Il timeout non è deducibile dalla board ricostruita** dopo un cache-miss (la mossa che ha flaggato non è mai stata applicata) — va recuperato da `result_override`, popolato in `_load_game_from_db()` confrontando `row.result` con l'esito deducibile da `_check_game_over(board)`: se differiscono (o quest'ultimo è `None`), l'esito persistito vince. Coperto da `test_flagged_result_survives_cache_miss`.
- **Risposta API.** `time_control`/`clock` sono **sempre presenti** nel body di `board_to_state` — mai chiavi assenti. Per una partita non a tempo: `time_control: null`, ma `clock: {"white": null, "black": null}` (non `null` a livello top, per struttura sempre valorizzata anche vuota — scelta di design della prima bozza, mantenuta invece di rifattorizzare per zero guadagno pratico; il frontend gestisce entrambe le forme controllando `time_control`).
- **Frontend** (`frontend/index.html`, nessun file nuovo): due box `.clock-box` fissi sopra/sotto la board (avversario sempre in alto, player sempre in basso — indipendente dalla rotazione per il nero, a differenza dell'orientamento dei pezzi). Selettore preset nel modal impostazioni (`.time-row`, stesso linguaggio visivo di `.side-row`). Countdown **previsionale** client-side (`setInterval` 250ms) che decrementa solo il lato al tratto (`state.turn`), sempre riconciliato col valore autoritativo del server a ogni risposta (`clockReconcile()`, chiamata da `updateState()`) — **nessun polling/websocket in questa fase** (quello arriva con l'item successivo di Fase 6): se il countdown locale tocca 0 senza che parta una nuova richiesta, resta fermo finché la prossima risposta non conferma la bandierina. Soglia "tempo basso" (classe `.low`, rosso pulsante) sotto i 10s. Bandierina → stesso flusso game-over esistente (`data.game_over`/suono `gameover`/banner), solo una nuova voce nella mappa `reasons` (`timeout: 'Tempo scaduto'`) — nessuna logica nuova lato UI di game-over.
- **Verifica.** Backend: 19 nuovi test in `TestTimeControl` (decremento, incremento, bandierina simmetrica player/engine, persistenza, cache-miss, regressione untimed) — 125/125 nella suite (baseline 106 + 19). Frontend: nessun browser disponibile nel sandbox, stessa tecnica jsdom delle fasi precedenti — `tests/frontend_harness.mjs` esteso con 11 check (selettore preset via click DOM reale, clock live contro un backend vero, countdown client-side dopo un `sleep`, riconciliazione post-mossa, bandierina iniettata via `updateState()` sintetico per non dover aspettare un timeout reale nel harness) — 57/57 check, incluso il resto della suite esistente invariato.

---

<a id="fase-7"></a>
### 🔲 Fase 7 — Coach Mode (Claude AI)
**Target: agosto/settembre 2026 · ~3 settimane · ~12 ore**

Obiettivo: modalità insegnamento con Claude come coach in tempo reale durante la partita contro Stockfish.
Analisi completa di design in [`docs/coach-mode.md`](docs/coach-mode.md).

| Settimana | Attività | Ore stimate | Modello suggerito |
|-----------|----------|-------------|-------------------|
| Sett. 4 ago | v1 on-demand: endpoint `POST /game/{id}/coach`, integrazione SDK Anthropic, system prompt con calibrazione ELO | ~4 ore | Opus |
| Sett. 4 ago | v1 frontend: pannello chat laterale, pulsante "Ask Coach", rendering hint | ~2 ore | Sonnet |
| Sett. 11 ago | v2 proactive: eval post-mossa, soglia cp loss, hint automatici opt-in, frequency cap | ~3 ore | Opus |
| Sett. 18 ago | v3 coach con memoria: integrazione statistiche Fase 5 e pattern di errore di Fase 4, hint personalizzati su errori ricorrenti | ~3 ore | Opus |

**Dipendenze:** Fase 5 completata (per v3, statistiche storiche), Fase 4 completata (pattern di errore ricorrenti), chiave API Anthropic, SDK `anthropic` Python.
**Nota:** questo è il coach *AI-based*. Il coach Stockfish-based, non-AI, è già stato completato in Fase 2 (Assisted Play).
**Modello consigliato:** Claude Haiku (costo ~$0.0004/partita on-demand, latenza <1s).
**Rischio principale:** prompt leaking del best move — mitigato non passando il best move nel contesto Claude.

---

<a id="fase-8"></a>
### ✅ Fase 8 — Modalità CLI / Companion — Wave 1+2 completate 22 luglio 2026
**Target: settembre/ottobre 2026 · ~3 settimane · ~16.5 ore**

Obiettivo: un compagno da terminale (REPL stile `claude`) che segue una partita giocata **altrove**
(Lichess, chess.com, scacchiera fisica) e fornisce consigli in tempo reale — mossa migliore, eval,
avvisi "pezzo in presa" — mentre l'utente riporta a mano le mosse di entrambi i lati (non
necessariamente quella suggerita). A fine partita: PGN e analisi errori, riusando gli endpoint
esistenti. Non un motore nuovo: una ricombinazione di `/hint`, `/threats`, `/game/analyze`,
`_build_pgn` ed ECO book attorno alla semantica "osserva e consiglia" invece di "gioca e rispondi".
Analisi completa di design in [`docs/cli-companion-mode-design.md`](docs/cli-companion-mode-design.md)
(domande aperte risolte il 21 luglio 2026).

**Wave 1 — MVP companion (~11 ore):**

**Nota di stato (22 luglio 2026): entrambe le wave sono chiuse, tutti i task implementati e mergiati
in `main`.** Wave 1: `feature/cli-companion-backend` (PR #37), `feature/cli-companion-cli` (PR #32,
21 luglio 2026), `feature/cli-companion-cli-commands` (PR #33, 21 luglio 2026), UI `rich`
`feature/cli-companion-cli-ui` (PR #38, 22 luglio 2026, `rich==15.0.0`, 236/236 test verdi). Wave 2:
`feature/cli-companion-wave2-bootstrap` (resume + input alternativi) e
`feature/cli-companion-wave2-autohint` (auto-hint a soglia) sono stati sviluppati in parallelo da due
subagent indipendenti e avevano aggiunto **ciascuno il proprio argparse** in `cli/__main__.py` —
riconciliati manualmente in un merge di integrazione (`feature/cli-companion-wave2`, PR #40) in un
solo `ArgumentParser`: `--resume`/`--fen`/`--pgn`/`--pgn-file` restano mutuamente esclusivi tra loro,
`--auto-hint-threshold` è un flag indipendente componibile con uno qualsiasi dei tre. Suite CLI finale:
**296/296 test verdi** (236 Wave 1 + 25 bootstrap + 33 auto-hint + 2 test di composizione aggiunti in
fase di merge).

| Settimana | Attività | Ore stimate | Modello suggerito | Stato |
|-----------|----------|-------------|-------------------|-------|
| — | Backend observer-mode: `source="companion"` (nessuna migration), endpoint `POST /game/companion/new` + `POST /game/{id}/companion/move` (SAN, riconosce anche UCI non ambiguo) + `POST /game/{id}/companion/undo`, loop di append estratto e condiviso con `/games/import` | ~3 ore | Opus | ✅ fatto e mergiato in `main` (branch `feature/cli-companion-backend`, PR #37) |
| — | CLI: scheletro REPL in `chess_app/cli/`, selezione effort→Skill (riusa `games.engine_elo`), Stockfish locale long-lived per i consigli, client di mirroring verso il backend, loop di consiglio con UX della mossa divergente | ~4 ore | Opus | ✅ fatto e mergiato in `main` (branch `feature/cli-companion-cli`, PR #32, 21 luglio 2026) |
| — | CLI: comandi `/pgn` e `/analyze` (mirror di endpoint esistenti) + riepilogo errori a fine partita | ~2 ore | Sonnet | ✅ fatto e mergiato in `main` (branch `feature/cli-companion-cli-commands`, PR #33, 21 luglio 2026; 219/219 test verdi alla verifica del 22 luglio 2026) |
| — | UI `rich`: spinner ricerca, pannelli eval/mossa migliore in place, evidenza "in presa"; nuova dipendenza in `requirements.txt` | ~2 ore | Sonnet | ✅ fatto e mergiato in `main` (branch `feature/cli-companion-cli-ui`, PR #38, 22 luglio 2026; `rich==15.0.0`; 236/236 test verdi, verificati direttamente) |

**Wave 2 — promosse dal backlog di design (~5.5 ore):**

| Settimana | Attività | Ore stimate | Modello suggerito | Stato |
|-----------|----------|-------------|-------------------|-------|
| — | Salva/riprendi una sessione companion interrotta (`--resume <game_id>`, riusa `GET /game/{id}` + cache-miss) | ~1.5 ore | Sonnet | ✅ fatto e mergiato in `main` (branch `feature/cli-companion-wave2-bootstrap`, integrata via PR #40) |
| — | Metodi di input alternativi: incollare una FEN (`start_fen`) o un PGN parziale come punto di partenza della sessione | ~2 ore | Sonnet | ✅ fatto e mergiato in `main` (stesso branch/PR di sopra — `--fen`/`--pgn`/`--pgn-file`, bootstrap client-side via `cli/pgn_bootstrap.py`, nessun endpoint nuovo) |
| — | Auto-hint con soglia (opt-in): consiglio mostrato automaticamente solo oltre una soglia di cp loss (es. −150cp) | ~2 ore | Sonnet | ✅ fatto e mergiato in `main` (branch `feature/cli-companion-wave2-autohint`, PR #39, 22 luglio 2026; `--auto-hint-threshold`, default invariato) |

**Dipendenze:** nessuna sulle fasi precedenti — riusa endpoint già esistenti (`/hint`, `/threats`,
`/game/analyze`, `_build_pgn`, ECO book) senza modificarli. Indipendente da Fase 7, collocabile prima
o dopo in base a priorità.
**Nota architetturale:** client hybrid — Stockfish locale nella CLI solo per il loop di consiglio a
bassa latenza (deroga esplicita e circoscritta al vincolo "un'istanza per chiamata API", motivata nel
design doc §4.1: la CLI è un processo separato, single-user, single-thread, nessuna concorrenza da cui
proteggersi); tutto il resto (persistenza, PGN, analisi, stats) resta via REST verso il backend
esistente, invariato.

---

<a id="timeline"></a>
### 📅 Timeline riepilogativa

```
Aprile 2026
├── Sett. 14 apr  ████  MVP API-only pronto          ✅ completato
├── Sett. 14 apr  ████  Fase 1 chiusa (FE + test)    ✅ completato
├── Sett. 21 apr  ████  Fase 2 — hint engine + toggle assisted mode  ✅ completato
└── Sett. 28 apr  ████  Fase 2 — eval bar + restyling Lichess-style ✅ completato

Maggio 2026
├── Sett. 5 mag   ████  Fase 3 — DB + storico            ✅ completato
├── Sett. 12 mag  ████  Fase 3 — replay + FE storico      ✅ completato
├── Sett. 19 mag  ████  Fase 4 — puzzle da blunder + spaced repetition  ✅ completato
└── Sett. 26 mag  ████  Fase 4 — profilo debolezze + drill finali  ✅ completato

Giugno 2026
├── Sett. 2 giu   ████  Fase 4 — frontend pannello Allenamento  ✅ completato
├── Sett. 9 giu   ████  Fase 5 — eval chart  ✅ completato (anticipato)
├── Sett. 16 giu  ████  Fase 5 — aperture ECO  ✅ completato
└── Sett. 23 giu  ████  Fase 5 — statistiche + dashboard  ✅ completato (anticipato in Fase 3)

Luglio 2026
├── Sett. 30 giu  ████  Fase 6 — puzzle trainer (dataset esterno)  ✅ completato
├── Sett. 7 lug   ████  Fase 6 — time control  ✅ completato (anticipato)
├── Sett. 14 lug  ████  Fase 6 — WebSocket  ✅ completato
└── (fuori roadmap) ██  Fase 4 — lezioni di teoria (backend+contenuto+FE)  ✅ completato 19 luglio 2026

Agosto 2026
├── Sett. 4 ago   ████  Fase 7 — coach on-demand (v1)
├── Sett. 11 ago  ████  Fase 7 — coach proactive (v2)
└── Sett. 18 ago  ████  Fase 7 — coach con memoria (v3)

Settembre 2026
├── Sett. 1 set   ████  Fase 8 — backend observer-mode companion
├── Sett. 8 set   ████  Fase 8 — CLI: scheletro REPL + loop di consiglio
└── Sett. 15 set  ████  Fase 8 — comandi /pgn /analyze + UI rich (Wave 1 chiusa)

Ottobre 2026
└── Sett. 1 ott   ████  Fase 8 — Wave 2 (resume, input alternativi, auto-hint a soglia)
```

**Prodotto completo stimato: metà ottobre 2026** (con 3–5 ore/settimana costanti, includendo Fase 8).
Slittamenti probabili: prompt tuning coach (Fase 7). (Fase 5 e il dataset Lichess puzzles di Fase 6 erano indicati qui come rischio ma sono entrambi completati.)
Buffer suggerito: +1 settimana per fase a partire dalla Fase 4.
