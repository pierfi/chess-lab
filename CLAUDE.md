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

Tabella compatta di sola consultazione — obiettivo e stato di ogni fase. Cronologia dettagliata
(tabelle settimana-per-settimana, note "Nota (...)", dettagli di implementazione non ovvi, timeline
riepilogativa) spostata in [`docs/roadmap.md`](docs/roadmap.md) per non appesantire ogni sessione con
narrativa storica — questo file resta focalizzato su ciò che serve per lavorare sul codice **oggi**
(contratti endpoint, schema DB, convenzioni frontend: vedi sezioni sotto).

| Fase | Obiettivo | Stato | Dettagli |
|------|-----------|-------|----------|
| MVP API-only | Backend funzionante: partita completa contro Stockfish + analisi via curl/HTTP | ✅ completato 16 apr 2026 | [`docs/roadmap.md#mvp`](docs/roadmap.md#mvp) |
| Fase 1 — Core engine & analisi | Partita completa contro Stockfish + analisi post-partita, backend e frontend stabili con test | ✅ completata 16 apr 2026 | [`docs/roadmap.md#fase-1`](docs/roadmap.md#fase-1) |
| Fase 2 — Assisted Play & Lichess UI | Coach non-AI in tempo reale (secondo Stockfish, MultiPV) + restyling UI stile Lichess | ✅ completata 7 lug 2026 | [`docs/roadmap.md#fase-2`](docs/roadmap.md#fase-2) |
| Fase 3 — Persistenza & storia | SQLite (5 tabelle) + storico/replay/import/delete + statistiche aggregate ed ELO simulato (anticipa parte di Fase 5) | ✅ completata 11 lug 2026 | [`docs/roadmap.md#fase-3`](docs/roadmap.md#fase-3) |
| Fase 4 — Allenamento mirato | Puzzle da blunder + spaced repetition (SM-2), profilo debolezze, drill di finali, lezioni di teoria | ✅ completata 11 lug 2026 (lezioni 19 lug) | [`docs/roadmap.md#fase-4`](docs/roadmap.md#fase-4) |
| Fase 5 — Analisi avanzata | Eval chart, identificazione apertura ECO, statistiche personali (due voci già coperte dall'anticipo di Fase 3) | ✅ completata 11–18 lug 2026 | [`docs/roadmap.md#fase-5`](docs/roadmap.md#fase-5) |
| Fase 6 — UX avanzata & real-time | Puzzle trainer da dataset Lichess esterno, time control, aggiornamenti live via WebSocket | ✅ completata 18 lug 2026 | [`docs/roadmap.md#fase-6`](docs/roadmap.md#fase-6) |
| Fase 7 — Coach Mode (Claude AI) | Coach AI in tempo reale (Claude) durante la partita contro Stockfish, calibrato sull'ELO | 🔲 non iniziata | [`docs/roadmap.md#fase-7`](docs/roadmap.md#fase-7) |
| Fase 8 — Modalità CLI / Companion | REPL da terminale che segue una partita giocata altrove (Lichess/chess.com/fisica) e consiglia in tempo reale | ✅ Wave 1 completata e mergiata 22 lug 2026 | [`docs/roadmap.md#fase-8`](docs/roadmap.md#fase-8) |

**Nota Fase 8 (22 luglio 2026):** tutti e 4 i task Wave 1 sono implementati e mergiati in `main`
(backend observer-mode `feature/cli-companion-backend` PR #37, scheletro CLI REPL
`feature/cli-companion-cli` PR #32, comandi `/pgn`/`/analyze` `feature/cli-companion-cli-commands`
PR #33, UI `rich` `feature/cli-companion-cli-ui` PR #38 — quest'ultima chiusa il 22 luglio 2026, 236/236
test verdi). Wave 1 è quindi chiusa; Wave 2 (resume sessione, input alternativi, auto-hint a soglia)
resta 🔲, non impegnata — dettagli completi in [`docs/roadmap.md#fase-8`](docs/roadmap.md#fase-8).

Timeline riassuntiva mese-per-mese: [`docs/roadmap.md#timeline`](docs/roadmap.md#timeline).

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
  "engine_elo": 1000,
  "opening": {"eco": "C60", "name": "Ruy Lopez"} | null
}
```
`opening` (Fase 5, `backend/eco_book.py`): longest-prefix match della cronologia mosse
contro `backend/data/eco.json` (822 righe curate). `null` se la posizione è già fuori
libro o se la partita parte da uno `start_fen` custom (drill di finali — il book è
costruito sulla posizione standard, non ha senso matchare un FEN arbitrario). Presente
anche su `GET /game/{id}/replay`.

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
Body: { "multipv": 3, "depth": 16, "hint_elo": 1300 }
# hint_elo opzionale (400–2800): calibra la forza del suggerimento (solo Skill
# Level, la depth resta req.depth). Omesso/null = piena forza (default).
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

### Statistiche aggregate Fase 3 (`/stats/*`) — implementati

Aggregazioni read-only su tutto lo storico persistito (dal DB, non dalla cache),
per la vista "sto migliorando?". Filtri condivisi con `GET /games` (fonte unica:
`_result_predicate`/`_player_result`/`_game_filter_conditions` in `main.py`):
`color`, `source` (default `play` — import/drill esclusi), `date_from`/`date_to`
(`YYYY-MM-DD` su `created_at`, `date_to` inclusivo del giorno intero; `400` se
formato errato). Spec autoritativa: [`docs/growth-analytics.md`](docs/growth-analytics.md).

```python
# Numeri headline. I tassi sono relativi alle partite DECISE (result non nullo);
# avg_accuracy media games.player_accuracy SOLO sulle partite analizzate
# (analyzed_at IS NOT NULL); avg_think_ms_per_move è sulle sole mosse del player
# (moves.color == games.player_color). null dove non c'è dato.
GET /stats/summary?color=white&source=play&date_from=2026-05-01&date_to=2026-05-31
Response: {
  "total_games": 42, "decided_games": 40, "analyzed_games": 30,
  "wins": 22, "losses": 15, "draws": 3,
  "win_rate": 0.55, "loss_rate": 0.375, "draw_rate": 0.075,
  "avg_accuracy": 76.4 | null,
  "total_blunders": 18, "total_mistakes": 41, "total_inaccuracies": 63,
  "avg_think_ms_per_move": 4200 | null
}

# Serie temporale per il grafico di crescita (frontend Fase 3). Un punto per
# partita DECISA in ordine cronologico, con ELO SIMULATO: update Elo classico
# (K=32, seed 1200) con engine_elo come rating avversario e result relativo a
# player_color come esito — proxy DIREZIONALE, non un rating rigoroso. simulated_elo
# è il rating DOPO la partita. In corso saltate; import esclusi (engine_elo=0).
GET /stats/progress?color=white&source=play
Response: {
  "seed_elo": 1200, "k_factor": 32, "games_counted": 40,
  "current_elo": 1287, "peak_elo": 1310,
  "series": [
    { "game_id": "6f0610a7", "date": "2026-07-11T10:00:00", "game_number": 1,
      "engine_elo": 1000, "result": "win", "score": 1.0,
      "simulated_elo": 1214, "accuracy": 78.5 | null }
  ],
  "recent": {
    "window": 10, "games": 10, "elo_change": 45,
    "avg_accuracy": 74.2 | null, "wins": 6, "losses": 3, "draws": 1
  }
}
```

### Endpoint Fase 4 (Allenamento mirato) — implementati

```python
# Prossima carta SRS scaduta, o un nuovo puzzle generato dal blunder/mistake
# più recente non ancora trasformato in carta (fallback a inaccuracy se non
# ce ne sono). `source` opzionale (default: nessun filtro) limita la
# GENERAZIONE di nuovi puzzle a un games.source specifico — non filtra la
# coda di ripasso, che resta a prescindere dalla partita di origine.
GET /training/puzzles/next?source=play
Response (puzzle disponibile): {
  "puzzle_id": 12,
  "game_id": "6f0610a7",
  "ply": 14,
  "fen": "r1bqkbnr/...",
  "player_to_move": "white" | "black",
  "source": "blunder" | "mistake" | "inaccuracy",
  "is_review": false,        # true se viene dalla coda SRS scaduta
  "due_at": null | "2026-07-20T10:00:00"   # solo se is_review=true
}
Response (nessuna carta scaduta e nessun candidato nuovo): {
  "puzzle_id": null,
  "message": "Nessuna carta in scadenza e nessun blunder/mistake nuovo da trasformare in puzzle."
}

# Risposta al puzzle: match esatto (case-insensitive) su move_uci vs
# best_move_uci, nessuna tolleranza cp. La carta SRS nasce qui al PRIMO
# tentativo (non alla generazione). Scheduling SM-2 semplificato — vedi
# l'algoritmo esatto in docs/training-mode.md.
POST /training/puzzles/{puzzle_id}/answer
Body: { "move_uci": "e2e4" }
Response: {
  "correct": true,
  "best_move_uci": "e2e4",
  "next_due_at": "2026-07-20T10:00:00",
  "interval_days": 3,
  "ease_factor": 2.7,
  "correct_streak": 2
}
# 404 se puzzle_id non esiste.

# Diagnosi debolezze: errori del PLAYER (non dell'engine) aggregati per fase
# di gioco (ply<=20 apertura; altrimenti materiale residuo<=13 → finale;
# resto → mediogioco) e tema tattico probabile (solo righe blunder/mistake).
# Euristiche python-chess approssimate, non un motore tattico — "note" lo
# ricorda esplicitamente. source default 'play', stessa convenzione di
# GET /games.
GET /training/weaknesses?source=play
Response: {
  "by_phase": {
    "opening":    {"avg_loss_cp": 12.3, "count": 45},
    "middlegame": {"avg_loss_cp": 34.1, "count": 120},
    "endgame":    {"avg_loss_cp": 58.7, "count": 30}
  },
  "by_theme": {
    "fork":        {"missed_count": 8},
    "pin":         {"missed_count": 5},
    "king_safety": {"missed_count": 12}
  },
  "note": "Euristiche approssimate su python-chess: temi probabili, non diagnosi certa."
}

# Lista statica dei drill di finali teorici (16 posizioni canoniche).
GET /training/endgames
Response: {
  "endgames": [
    {
      "id": "kr_vs_k",
      "name": "Re e Torre contro Re",
      "fen": "4k3/8/8/8/8/8/8/R3K3 w - - 0 1",
      "goal": "win" | "draw",
      "description": "Scaccomatto elementare con la tecnica della scala (torre + re)."
    }
  ]
}

# Avvia una partita dal FEN del drill scelto — riusa la stessa logica di
# creazione di POST /game/new (_create_new_game), fissando source a
# 'endgame_drill'. 404 se l'id non esiste. Se il colore scelto dal player non
# coincide col colore al tratto sul FEN del drill, l'engine apre la partita
# (stesso comportamento generalizzato di /game/new, non solo per la
# posizione standard).
POST /training/endgames/{endgame_id}/start
Body: { "player_color": "white"|"black", "engine_elo": 800 }
Response:  # stesso shape di board_to_state (GET /game/{id}) + endgame_id/goal
{
  "game_id": "a1b2c3d4",
  "fen": "4k3/8/8/8/8/8/8/R3K3 w - - 0 1",
  "pgn": "...",
  "turn": "white",
  "is_check": false,
  "is_game_over": false,
  "result": null,
  "last_engine_move": null,
  "move_history": [],
  "move_history_san": [],
  "player_color": "white",
  "engine_elo": 800,
  "endgame_id": "kr_vs_k",
  "goal": "win"
}

# Lezioni di teoria (aggiunte 19 luglio 2026, docs/theory-lessons-design.md):
# strato didattico "a monte" di puzzle/drill/debolezze. Contenuto statico
# curato a mano in backend/data/lessons.json (6 lezioni: 2 aperture, 2
# tattiche, 2 tecniche di finale), read-only, nessuna scrittura DB.
GET /training/lessons
Response: {
  "lessons": [
    { "id": "italiana-idee", "title": "L'Apertura Italiana: sviluppo e pressione su f7",
      "category": "opening", "level": "beginner",
      "summary": "Le idee di base del Giuoco Piano: centro, sviluppo e il punto debole f7." }
  ]
}

# Dettaglio con la sequenza di FEN già espansa via python-chess (stesso
# pattern di GET /game/{id}/replay). Le mosse sono autorate in SAN nel
# JSON; uci è derivato dal backend, non salvato a mano.
GET /training/lessons/{lesson_id}
Response: {
  "id": "italiana-idee", "title": "...", "category": "opening", "level": "beginner",
  "orientation": "white", "intro": "...", "start_fen": "rnbqkbnr/.../8 w KQkq - 0 1",
  "fens": ["<fen dopo 0 mosse>", "..."],   # N+1 FEN, come replay
  "line": [
    { "ply": 1, "uci": "e2e4", "san": "e4", "mode": "show", "comment": "..." },
    { "ply": 5, "uci": "f1c4", "san": "Bc4", "mode": "play",
      "prompt": "Sviluppa l'alfiere...", "comment": "..." }
  ],
  "related_drill_id": "lucena" | null   # collega la lezione al drill omonimo in ENDGAME_DRILLS
}
# 404 se lesson_id non esiste.
```

**Note non ovvie:** validazione di legalità dell'intera linea SAN fatta al caricamento (fallisce rumorosamente all'avvio se una lezione è malformata, mai in produzione). Il passo "play" viene validato **lato client** con un confronto stringa UCI (nessun endpoint di risposta, la soluzione è già nei dati fetchati) — diverso dai puzzle self-generated, che devono passare dal backend perché non conoscono la soluzione. `related_drill_id` collega `lucena-ponte` al drill `lucena` esistente (stesso FEN); le altre 5 lezioni non hanno drill collegato (`null`). Analisi di design completa: [`docs/theory-lessons-design.md`](docs/theory-lessons-design.md). Percorsi di apprendimento legati all'ELO (idea "da ELO 600 a 1000") sono **solo un documento di design** al momento, non implementati: [`docs/theory-lesson-paths-design.md`](docs/theory-lesson-paths-design.md).

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
- Pezzi: asset SVG statici, set "Cburnett" di Lichess (`frontend/pieces/*.svg`, 12 file — vedi `pieces/NOTICE.md` per licenza/provenienza). Non più glifi Unicode: la resa a font-dipendeva dal sistema dell'utente e non garantiva colori distinguibili (`docs/bugs.md` Bug #6, fix v1/v2 insufficienti, fix v3 risolutivo l'11 luglio 2026). Mappa carattere FEN → file in `PIECE_FILES`, creazione `<img>` via `pieceImg(fenChar)`, usata da `renderBoard()` e `askPromotion()`.

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
- **Regola ferrea sui commit: MAI includere una riga `Co-Authored-By: Claude ...` (o equivalente) nei messaggi di commit.** Vale per ogni sessione, ogni subagent (Fable, Sonnet, Opus, ecc.) e ogni branch/worktree di questo repo, senza eccezioni.

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
