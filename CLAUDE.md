# Chess Lab — CLAUDE.md

Documento di contesto per Claude Code. Leggi tutto prima di toccare codice.

---

## Panoramica progetto

**Chess Lab** è un'applicazione full-stack per imparare e analizzare gli scacchi.
Architettura: FastAPI backend + HTML/JS frontend (zero dipendenze npm) + Stockfish 16 via UCI.

**Stack:**
- Backend: Python 3.12, FastAPI, python-chess, Stockfish UCI (`/usr/games/stockfish`)
- Frontend: HTML5 + CSS3 + JavaScript vanilla (nessun framework, nessun bundler)
- Storage attuale: in-memory (dict Python) — da migrare a SQLite in Fase 3
- Engine: Stockfish 16, comunicazione UCI via `chess.engine.SimpleEngine`

**Struttura directory:**
```
chess_app/
├── backend/
│   └── main.py            # FastAPI app, tutti gli endpoint
├── frontend/
│   └── index.html         # Intera UI in un singolo file
├── tests/
│   └── test_api.py        # Test pytest (da creare)
└── README.md
```

---

## Roadmap fasi

> **Assunzione:** 3–5 ore/settimana, supervisione attiva su ogni step con Claude Code.
> Le stime includono tempo di review, piccoli fix manuali e test.
> La data di riferimento è **aprile 2026**.

---

### ✅ MVP API-only — completato 16 aprile 2026

Backend funzionante: partita completa contro Stockfish + analisi via curl/HTTP.

- [x] Fix SAN in `analyze_game()`
- [x] Gestione tutti i game-over (stalemate, 50 mosse, ripetizione, materiale insufficiente)
- [x] Gestione promozione pedone in `POST /game/move`
- [x] Test pytest end-to-end (13 test)

---

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

### ✅ Fase 2 — Assisted Play & Lichess UI — completata 7 luglio 2026

Partita "assistita" — un secondo Stockfish (analysis engine, separato da quello che gioca) mostra in tempo reale la mossa migliore, l'eval e le mosse candidate, esattamente come la scacchiera di analisi Lichess dopo una partita. Coach non-AI: nessuna dipendenza esterna, nessuna chiave API. Restyling UI in stile Lichess (screenshot di riferimento forniti dall'utente il 7 luglio 2026), rifinito su feedback diretto dell'utente.

- [x] `POST /game/{id}/hint` — analysis engine separato (MultiPV), best move + top N mosse candidate + eval, senza toccare lo stato partita (19/19 test passano)
- [x] Toggle "Assisted Mode" per-partita, nessun impatto sul flusso mossa-engine esistente
- [x] Frontend: overlay SVG per frecce sulla board (mappatura casella→coordinate coerente con la rotazione per il nero, `sqToXY()` inverso di `sqName()`) + eval bar verticale live con anti-race (`hintSeq`)
- [x] Restyling UI stile Lichess: modal impostazioni partita, tema chiaro, toolbar compatta, coordinate dentro le caselle di bordo

Note tecniche:
- L'engine "assist" è un'istanza separata da quella che gioca contro il player (coerente con il vincolo "un'istanza Stockfish per chiamata API, nessun engine globale").
- MultiPV per ottenere le top N mosse candidate, non solo la migliore.
- La mossa suggerita non viene mai giocata automaticamente: resta una sovrapposizione visiva, l'utente sceglie sempre la mossa.
- Nessuna IA/LLM coinvolta in questa fase — è puro output Stockfish. Il coach AI-based (Claude) resta pianificato separatamente in Fase 7.
- **Hint-engine a piena forza**: NON deve ereditare `elo_to_skill_depth` del play-engine — va configurato senza Skill Level limitato (o Skill 20), altrimenti i suggerimenti sarebbero deboli/incoerenti col resto dell'app.
- **Anti-latenza**: a depth 16 con MultiPV=3, `/hint` costa ~1-2s a chiamata. Va invocato on-demand (bottone/toggle esplicito) o con debounce — mai automaticamente dopo ogni mossa, per non raddoppiare l'attesa già presente per la mossa del play-engine.
- **Coerenza dati, non engine**: gli endpoint FastAPI restano sincroni (`def`) e girano nel threadpool, quindi `/hint` e `/game/move` possono sovrapporsi sullo stesso `games[game_id]["board"]`. I due processi Stockfish sono isolati e va bene, ma `/hint` può leggere un FEN che sta per essere superato da una `push()` concorrente. Impatto basso (l'hint non muta stato), da tenere presente se emergono risposte stale.

---

### 🔄 Fase 3 — Persistenza & storia (backend completato, resta il frontend)
**Target: inizio-metà maggio 2026 · ~2 settimane · ~8 ore**

Obiettivo: le partite sopravvivono al riavvio del server. Storico consultabile e replay.

**Stato:** fondazione di persistenza + **tutti gli endpoint backend di storico/replay/delete/import/analisi** sono **completati** (11 luglio 2026, branch `feature/history-analytics-api`). Resta solo il frontend (pagina storico, replay con frecce, import PGN lato UI) in una fase successiva.

**Nota:** l'**export** PGN (scaricare la partita corrente come `.pgn`) è stato anticipato l'11 luglio 2026, fuori roadmap — puro frontend, il backend genera già il PGN in ogni risposta di stato. Vedi [`docs/improvements.md`](docs/improvements.md). L'**import** PGN ha ora il backend pronto (`POST /games/import`, vedi sotto); resta solo l'import lato UI.

| Settimana | Attività | Ore stimate | Modello suggerito | Stato |
|-----------|----------|-------------|-------------------|-------|
| Sett. 5 mag | Schema SQLite + SQLAlchemy (5 tabelle) + migration Alembic iniziale | ~3 ore | Opus | ✅ fatto |
| Sett. 5 mag | Write-through cache + persistenza `games`/`moves` con think time su `/game/new` e `/game/move`; `start_fen` su `NewGameRequest` | ~2 ore | Opus | ✅ fatto |
| Sett. 5 mag | `GET /games` con paginazione, `DELETE /game/{id}` | ~1.5 ore | Sonnet | ✅ fatto |
| Sett. 12 mag | `GET /game/{id}/replay` (sequenza FEN) | ~1.5 ore | Sonnet | ✅ fatto |
| Sett. 12 mag | Persistenza risultati `/game/analyze` → `analysis_results` (colonne già presenti) | ~1 ora | Sonnet | ✅ fatto |
| Sett. 12 mag | Backend `POST /games/import` (parsing PGN esterno, non a roadmap in origine ma raggruppato qui perché stessa area di storico) | — | Sonnet | ✅ fatto |
| Sett. 12 mag | Frontend: pagina storico, replay con frecce, import PGN | ~2 ore | Opus | 🔲 da fare |

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

---

### 🔲 Fase 4 — Allenamento mirato: errori, ripasso e finali
**Target: metà-fine maggio 2026 · ~3 settimane · ~14 ore**

Obiettivo: trasformare gli errori giocati in materiale di allenamento reale, non solo in statistiche a consuntivo. Puzzle generati dai propri blunder, ripasso a intervalli (spaced repetition), diagnosi delle debolezze per fase di gioco e tema tattico, drill di finali teorici. Dipende dalla persistenza di Fase 3 (`analysis_results`). Analisi di design completa in [`docs/training-mode.md`](docs/training-mode.md).

| Settimana | Attività | Ore stimate | Modello suggerito |
|-----------|----------|-------------|-------------------|
| Sett. 19 mag | Schema `puzzles` + `srs_cards`; generazione puzzle da `analysis_results` (ogni mossa con `classification` blunder/mistake diventa un puzzle: FEN prima dell'errore + `best_move_uci`) | ~3 ore | Opus |
| Sett. 19 mag | `GET /training/puzzles/next`, `POST /training/puzzles/{id}/answer` con scheduling SM-2 semplificato | ~3 ore | Opus |
| Sett. 26 mag | `GET /training/weaknesses` — aggregazione errori per fase (apertura/mediogioco/finale) e tema tattico (fork/pin/re esposto) da `analysis_results` | ~3 ore | Opus |
| Sett. 26 mag | Drill finali teorici: `GET /training/endgames` (lista statica ~15-20 FEN canonici), `POST /training/endgames/{id}/start` (estende `POST /game/new` con `start_fen` opzionale) | ~2 ore | Sonnet |
| Sett. 2 giu | Frontend: pannello "Allenamento" — risoluzione puzzle, dashboard debolezze, selezione drill finali | ~3 ore | Opus |

Tabelle DB (in aggiunta a quelle di Fase 3):
- `puzzles` — id, game_id, ply, fen, best_move_uci, source (`blunder`\|`mistake`), created_at
- `srs_cards` — id, puzzle_id, due_at, interval_days, ease_factor, correct_streak, last_reviewed_at

**Nota:** questi puzzle nascono dalle proprie partite (self-generated) — concettualmente distinti dalla "Modalità puzzle" di Fase 6 (dataset Lichess esterno, FEN generiche). Le due funzionalità convivono, non si sovrappongono.

---

### 🔲 Fase 5 — Analisi avanzata
**Target: giugno 2026 · ~3 settimane · ~10 ore**

Obiettivo: trasformare l'app in un vero trainer con feedback quantitativo sui progressi.

| Settimana | Attività | Ore stimate | Modello suggerito |
|-----------|----------|-------------|-------------------|
| Sett. 9 giu | ✅ Grafico eval: curva centipawn, highlight blunders, click → jump mossa — **anticipato, completato l'11 luglio 2026** su `feature/analysis-panel-v2` insieme al restyling a due colonne del pannello analisi (vedi [docs/improvements.md](docs/improvements.md)) | ~3 ore | Opus |
| Sett. 16 giu | Identificazione apertura ECO live (eco.json locale, ~500 aperture) | ~2.5 ore | Sonnet |
| Sett. 23 giu | Statistiche personali: accuracy storica, errori frequenti, ELO simulato | ~3 ore | Opus |
| Sett. 23 giu | Dashboard riepilogo (ultimi 10 match, trend accuracy) | ~1.5 ore | Sonnet |

---

### 🔲 Fase 6 — UX avanzata & real-time
**Target: fine giugno / luglio 2026 · ~3 settimane · ~10 ore**

Obiettivo: funzionalità avanzate per rendere il training più vario e coinvolgente.

| Settimana | Attività | Ore stimate | Modello suggerito |
|-----------|----------|-------------|-------------------|
| Sett. 30 giu | Modalità puzzle: FEN custom, mossa corretta unica, feedback immediato | ~4 ore | Opus |
| Sett. 7 lug | Time control: clock digitale, bullet/blitz/rapid, Fischer increment | ~3 ore | Sonnet |
| Sett. 14 lug | WebSocket: aggiornamenti live, supporto multi-tab | ~3 ore | Opus |

**Nota:** il dataset Lichess puzzles (CSV ~50 MB) richiede un import script separato e uno schema dedicato. Valutare se incluso in Fase 6 o posticipato. Puzzle da dataset esterno, distinti dai puzzle self-generated di Fase 4.

---

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

### 📅 Timeline riepilogativa

```
Aprile 2026
├── Sett. 14 apr  ████  MVP API-only pronto          ✅ completato
├── Sett. 14 apr  ████  Fase 1 chiusa (FE + test)    ✅ completato
├── Sett. 21 apr  ████  Fase 2 — hint engine + toggle assisted mode  ✅ completato
└── Sett. 28 apr  ████  Fase 2 — eval bar + restyling Lichess-style ✅ completato

Maggio 2026
├── Sett. 5 mag   ████  Fase 3 — DB + storico  ← prossimo
├── Sett. 12 mag  ████  Fase 3 — replay + FE storico
├── Sett. 19 mag  ████  Fase 4 — puzzle da blunder + spaced repetition
└── Sett. 26 mag  ████  Fase 4 — profilo debolezze + drill finali

Giugno 2026
├── Sett. 2 giu   ████  Fase 4 — frontend pannello Allenamento
├── Sett. 9 giu   ████  Fase 5 — eval chart
├── Sett. 16 giu  ████  Fase 5 — aperture ECO
└── Sett. 23 giu  ████  Fase 5 — statistiche + dashboard

Luglio 2026
├── Sett. 30 giu  ████  Fase 6 — puzzle trainer (dataset esterno)
├── Sett. 7 lug   ████  Fase 6 — time control
└── Sett. 14 lug  ████  Fase 6 — WebSocket

Agosto 2026
├── Sett. 4 ago   ████  Fase 7 — coach on-demand (v1)
├── Sett. 11 ago  ████  Fase 7 — coach proactive (v2)
└── Sett. 18 ago  ████  Fase 7 — coach con memoria (v3)
```

**Prodotto completo stimato: fine agosto 2026** (con 3–5 ore/settimana costanti).
Slittamenti probabili: Fase 5 (complessità statistica), dataset Lichess puzzles (volume dati, Fase 6), prompt tuning coach (Fase 7).
Buffer suggerito: +1 settimana per fase a partire dalla Fase 4.

---

## Backend — Dettagli implementazione

### `backend/main.py`

**Costanti:**
```python
STOCKFISH_PATH = "/usr/games/stockfish"  # Linux
# macOS: "/usr/local/bin/stockfish"
```

**Struttura game state (in-memory):**
```python
games[game_id] = {
    "board": chess.Board(),          # oggetto python-chess
    "player_color": "white"|"black",
    "engine_elo": int,               # 400–2800
    "move_objects": [chess.Move],    # lista ordinata di mosse
    "last_engine_move": str|None,    # UCI dell'ultima mossa engine
    "created_at": str,               # "YYYY.MM.DD"
}
```

**Mapping ELO → Stockfish Skill Level:**
```
ELO < 800   → Skill 0,  depth 1
ELO < 1000  → Skill 3,  depth 3
ELO < 1200  → Skill 6,  depth 5
ELO < 1400  → Skill 9,  depth 7
ELO < 1600  → Skill 12, depth 9
ELO < 1800  → Skill 15, depth 12
ELO < 2000  → Skill 18, depth 15
ELO >= 2000 → Skill 20, depth 20
```

**Classificazione mosse (centipawn loss dalla parte del giocatore che muove):**
```
loss >= 200  → blunder
loss >= 80   → mistake
loss >= 30   → inaccuracy
loss >= -10  → good
loss <  -10  → excellent
```

**Risposta tipo `board_to_state`:**
```json
{
  "game_id": "6f0610a7",
  "fen": "rnbqkbnr/...",
  "pgn": "[Event ...]",
  "turn": "white"|"black",
  "is_game_over": false,
  "result": null | "1-0" | "0-1" | "1/2-1/2",
  "last_engine_move": "e7e5" | null,
  "move_history": ["e2e4", "e7e5", ...],
  "player_color": "white"|"black",
  "engine_elo": 1000
}
```

**Risposta tipo `/game/analyze`:**
```json
{
  "game_id": "...",
  "total_moves": 24,
  "blunders": 1,
  "mistakes": 2,
  "inaccuracies": 3,
  "accuracy_score": 78.5,
  "moves": [
    {
      "ply": 1,
      "move_number": 1,
      "color": "white",
      "move_uci": "e2e4",
      "move_san": "e4",
      "best_move_uci": "e2e4",
      "score_cp": 18,
      "loss_cp": 0,
      "classification": "excellent"
    }
  ]
}
```

### Endpoint da implementare in Fase 2 (Assisted Play)

```python
# Hint live: analysis engine separato (MultiPV), non tocca lo stato partita
POST /game/{id}/hint
Body: { "multipv": 3, "depth": 16 }
Response: {
  "eval_cp": 34,               # dal punto di vista del bianco
  "lines": [
    { "move_uci": "e2e4", "move_san": "e4", "score_cp": 34 },
    { "move_uci": "d2d4", "move_san": "d4", "score_cp": 28 },
    { "move_uci": "g1f3", "move_san": "Nf3", "score_cp": 22 }
  ]
}
```

### Endpoint Fase 3 (Persistenza) — implementati

```python
# Lista paginata/filtrata delle partite (dal DB, non dalla cache in-memory).
# result è relativo a player_color, non alla stringa PGN grezza:
#   win  → (player_color=white AND games.result='1-0') OR (player_color=black AND games.result='0-1')
#   loss → l'inverso
#   draw → games.result='1/2-1/2'
# source di default: SOLO 'play' (endgame_drill/import esclusi se non richiesti esplicitamente).
GET /games?page=1&per_page=20&color=white&result=win&source=play
Response: {
  "items": [
    {
      "game_id": "6f0610a7",
      "created_at": "2026-07-11T10:00:00",
      "finished_at": "2026-07-11T10:05:00" | null,
      "player_color": "white",
      "engine_elo": 1000,
      "result": "1-0" | null,
      "result_reason": "checkmate" | null,
      "move_count": 24,
      "analyzed_at": "2026-07-11T10:06:00" | null,
      "player_accuracy": 78.5 | null,
      "blunders": 1 | null,
      "mistakes": 2 | null,
      "inaccuracies": 3 | null
    }
  ],
  "page": 1,
  "per_page": 20,
  "total": 42
}

# Replay: sequenza di FEN (da moves.fen_before + posizione finale), mosse e PGN.
GET /game/{id}/replay
Response: {
  "fens": ["rnbqkbnr/.../8 w KQkq - 0 1", "...", ...],   # N mosse + 1 (posizione finale)
  "moves": [
    { "ply": 1, "uci": "e2e4", "san": "e4", "think_ms": 850 }
  ],
  "pgn": "[Event ...]"
}

# Cancella partita: riga games + cascade DB su moves/analysis_results/puzzles/
# srs_cards (ON DELETE CASCADE, verificato in pratica) + eviction dalla cache.
DELETE /game/{id}
Response: { "deleted": true, "game_id": "6f0610a7" }
# 404 se la partita non esiste.

# Import PGN esterno. Nessuna analisi automatica (resta una chiamata separata
# a /game/analyze). Convenzioni: player_color sempre "white" (nessun vero
# "player" locale in un import), engine_elo sentinella 0 ("sconosciuto").
POST /games/import
Body: { "pgn": "[Event ...]\n\n1. e4 e5 2. Nf3 ..." }
Response:  # stesso shape di board_to_state (GET /game/{id}) + "source"
{
  "game_id": "a1b2c3d4",
  "fen": "...",
  "pgn": "...",
  "turn": "white" | "black",
  "is_check": false,
  "is_game_over": false,
  "result": null,
  "last_engine_move": null,
  "move_history": ["e2e4", "e7e5", ...],
  "move_history_san": ["e4", "e5", ...],
  "player_color": "white",
  "engine_elo": 0,
  "source": "import"
}
# 400 se il PGN è vuoto/senza mosse o non parsabile.
```

Persistenza analisi (additiva, non cambia la risposta esistente di `/game/analyze`):
upsert per-ply in `analysis_results` (unique `game_id`+`ply`, idempotente) +
aggiornamento di `games.analyzed_at`/`player_accuracy`/`blunders`/`mistakes`/`inaccuracies`.

### Endpoint da implementare in Fase 4 (Allenamento mirato)

```python
# Prossimo puzzle da ripassare (SRS) o generato da un blunder/mistake recente
GET /training/puzzles/next
# → {"puzzle_id": ..., "fen": "...", "player_to_move": "white"|"black", "source": "blunder"|"mistake"}

# Risposta al puzzle: valida la mossa, aggiorna lo scheduling SM-2
POST /training/puzzles/{puzzle_id}/answer
Body: { "move_uci": "e2e4" }
Response: { "correct": true, "best_move_uci": "e2e4", "next_due_at": "2026-05-24" }

# Diagnosi debolezze: errori aggregati per fase di gioco e tema tattico
GET /training/weaknesses
# → {"by_phase": {"opening": {...}, "middlegame": {...}, "endgame": {...}},
#     "by_theme": {"fork": {...}, "pin": {...}, "king_safety": {...}}}

# Lista drill di finali teorici (FEN statici)
GET /training/endgames

# Avvia una partita da un FEN custom (drill finale)
POST /training/endgames/{id}/start
# → riusa POST /game/new estendendolo con `start_fen` opzionale
```

Analisi di design completa (algoritmo SM-2, classificazione fase/tema, formato guida utente): [`docs/training-mode.md`](docs/training-mode.md).

---

## Frontend — Dettagli implementazione

### `frontend/index.html`

File singolo, nessuna dipendenza esterna. Tutto inline (CSS + JS).

**Costante API:**
```javascript
const API = 'http://localhost:8765';
```

**State globale:**
```javascript
let state = {
  gameId: null,
  fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
  playerColor: 'white',
  engineElo: 1000,
  selectedSq: null,        // nome casella selezionata es. "e2"
  legalMoves: [],          // [{from, to, promo: bool}]
  lastMove: null,          // {from, to}
  moveHistory: [],         // array di stringhe UCI
  isGameOver: false,
  turn: 'white',
  thinking: false,         // true mentre Stockfish calcola
};
```

**Board rendering:**
- Indice `i` (0–63) → nome casella dipende da `state.playerColor`
- Bianco: riga 0 = rank 8, col 0 = file a → `sqName(i)` restituisce es. "a8" per i=0
- Nero: board ruotata, riga 0 = rank 1, col 0 = file h
- Pezzi: Unicode (`♔♕♖♗♘♙` / `♚♛♜♝♞♟`)

**CSS variables principali:**
```css
--bg:       #0e0f13
--surface:  #161820
--card:     #1e2030
--accent:   #c9a84c   /* oro */
--white-sq: #f0d9b5
--black-sq: #b58863
--green:    #4caf82
--red:      #e64c4c
--orange:   #e67c4c
--yellow:   #e6b84c
--blue:     #4c8ce6
```

**Highlights sulla board:**
- `.selected` — casella selezionata (sfondo dorato 55%)
- `.legal-move` — dot circolare (destinazione vuota)
- `.legal-capture` — anello (destinazione occupata da avversario)
- `.last-move` — sfondo dorato 28%
- `.king-check` — sfondo rosso 50%

**Generazione mosse candidate (client-side):**
La funzione `generateMoveCandidates(fen, fromSq, playerColor)` è una heuristica visiva. La validazione reale è sempre sul backend. Se il backend risponde 400, la mossa è illegale e va ignorata silenziosamente.

**Promozione pedone:**
Modal con 4 pezzi (Q/R/B/N). Ritorna una Promise che risolve con il carattere UCI (`'q'|'r'|'b'|'n'`).
L'UCI completo viene costruito come `fromSq + toSq + promoChar` es. `"e7e8q"`.

---

## Testing

### Dipendenze
```bash
pip install pytest httpx pytest-asyncio
```

### File `tests/test_api.py` da implementare

Coprire almeno questi scenari:

```python
# test_new_game: player white/black, elo valido/invalido
# test_make_move: mossa legale, mossa illegale, promozione
# test_game_over: checkmate, stalemate, draw
# test_analyze: partita corta (3 mosse), partita con blunder evidente
# test_pgn_export: verifica formato PGN valido
```

Esempio fixture base:
```python
import pytest
from fastapi.testclient import TestClient
from backend.main import app

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def new_game(client):
    r = client.post("/game/new", json={"player_color": "white", "engine_elo": 800})
    return r.json()
```

---

## Comandi utili

```bash
# Avvio backend (sviluppo)
cd chess_app/backend
uvicorn main:app --reload --port 8765

# Avvio backend (produzione)
uvicorn main:app --host 0.0.0.0 --port 8765 --workers 1

# Test
cd chess_app
pytest tests/ -v

# Frontend (nessun build step)
open frontend/index.html
# oppure
python -m http.server 3000 --directory frontend

# Health check
curl http://localhost:8765/health

# Test rapido manuale
curl -s -X POST http://localhost:8765/game/new \
  -H "Content-Type: application/json" \
  -d '{"player_color":"white","engine_elo":800}' | python -m json.tool
```

---

## Vincoli & decisioni architetturali

**Backend:**
- Un'istanza Stockfish per chiamata API (apertura/chiusura nel `with`). Non tenere engine in memoria globale per evitare race condition.
- `game_id` = primi 8 caratteri di UUID4. Abbastanza per uso locale, da estendere in produzione.
- CORS aperto (`allow_origins=["*"]`). Restringere in produzione.
- In Fase 3, usare SQLAlchemy con SQLite. Non usare raw SQL. Schema da definire con Alembic per migration.
- Analisi: depth default 16. Non superare 20 per non bloccare il thread su partite lunghe. Gli endpoint sono già `def` sincroni, quindi FastAPI li gira nel threadpool automaticamente — nessun bisogno di `run_in_executor` esplicito.
- Fase 2: l'analysis engine per `/hint` è un'istanza Stockfish separata da quella che gioca — stesso vincolo "un'istanza per chiamata", nessun engine globale condiviso. Attenzione: essendo `/hint` e `/game/move` entrambi sincroni nel threadpool, possono sovrapporsi sullo stesso `games[game_id]["board"]` — impatto basso perché `/hint` non muta stato, ma tenerlo presente se emergono FEN stale nella risposta.

**Frontend:**
- Nessun framework, nessun bundler. Il file `index.html` deve rimanere apribile direttamente nel browser senza server (tranne chiamate API).
- Non usare `localStorage` o `sessionStorage` per lo stato partita — tutto va in `state` in memoria.
- La board è un grid CSS 8×8 di `<div>`. Non usare canvas.
- Le mosse legali mostrate al client sono euristiche visive. La fonte di verità è sempre il backend.

**Generale:**
- Lingua commenti: italiano per commenti architetturali/business, inglese per commenti tecnici inline.
- Nessuna dipendenza npm/yarn. Se serve una libreria JS in futuro, usare CDN via `<script>`.
- Il progetto è educational/locale: nessuna auth, nessun rate limiting, nessun deploy cloud per ora.

---

## Bug noti & TODO immediati (Fase 1) — tutti risolti

| # | Descrizione | Stato |
|---|-------------|-------|
| 1 | `move_san` in `/game/analyze` ritornava UCI invece di SAN | Fixato — SAN calcolata prima di `board.push()` |
| 2 | Game-over per stalemate non gestito | Fixato — `_check_game_over()` copre tutti i casi |
| 3 | Promozione: pezzi del colore sbagliato nel modal | Fixato — `askPromotion()` riceve il colore |
| 4 | `generateMoveCandidates` non gestiva en passant | Fixato — parsing campo EP dal FEN |
| 5 | Nessun test automatico | Fixato — 13 test in `test_api.py` |

---

## Glossario

| Termine | Significato |
|---------|-------------|
| UCI | Universal Chess Interface — protocollo testuale per comunicare con engine |
| SAN | Standard Algebraic Notation — es. `Nf3`, `O-O`, `e4` |
| PGN | Portable Game Notation — formato standard per salvare partite |
| FEN | Forsyth-Edwards Notation — stringa che descrive una posizione scacchistica |
| Centipawn (cp) | Unità di misura del vantaggio: 100 cp = 1 pedone di vantaggio |
| Ply | Mezza mossa (una mossa di un singolo colore) |
| Blunder | Errore grave: perdita ≥ 200 cp |
| ELO | Sistema di rating usato per stimare la forza di un giocatore |
