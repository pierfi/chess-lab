# Chess Lab — Stato sessione / roadmap di ripresa

Documento leggero, aggiornato ad ogni pausa di sessione, per riprendere il lavoro senza dover
rileggere l'intera cronologia. Per il piano di fase completo (schema DB, endpoint, design)
vedi `CLAUDE.md` → sezione "Roadmap fasi" e la memoria di progetto
`project_chess_lab_persistence_analytics`. Questo file è solo lo **snapshot dei branch in volo**.

Ultimo aggiornamento: **17 luglio 2026**.

**Bug #8/#9 fixati (17 luglio 2026, branch `fix/analyze-start-fen`):** `POST /game/analyze` ora onora `start_fen` (`_starting_board()` sostituisce le due `chess.Board()` hardcodate) e `move_number` deriva correttamente il turno iniziale da `board.turn` invece di assumere sempre Bianco-first. 4 nuovi test di regressione in `tests/test_api.py` (`TestAnalyzeStartFen` + un test in `TestImportPgn`), 97/97 test verdi (93 preesistenti + 4 nuovi). Verificato che i nuovi test vanno in hang/timeout sul codice pre-fix e passano in ~3s su quello fixato. Vedi `docs/bugs.md` per i dettagli.

---

## Rami attivi e stato

| Branch | Stato | Note |
|--------|-------|------|
| `fix/piece-svg-set` | ✅ **Merged in `main`** (PR #4) | Fix v3 pezzi (SVG Cburnett). Bug #6 **confermato visivamente dall'utente l'11 luglio 2026 — chiuso**. |
| `investigate/en-passant-move10` | ✅ **Merged in `main`** (PR #5) | Segnalazione "mossa illegale" `10. cxd6` verificata come en passant legale (non bug). Conflitto con `main` su `docs/bugs.md` risolto e pushato (merge `a6ebdba`), 21/21 test verdi. |
| `feature/hint-engine-strength` | ✅ **Merged in `main`** (PR #6) | Forza regolabile dell'hint engine (Fable). `hint_elo` opzionale su `POST /game/{id}/hint`, default invariato (piena forza). 24/24 test verdi. |
| `feature/persistence-db` | ✅ **Merged in `main`** (PR #7) | Fase 1 (persistenza SQLite): schema 5 tabelle, write-through cache, `think_ms`. Base per tutte le fasi successive. |
| `feature/history-analytics-api` | ✅ **Merged in `main`** (PR #8) | Fase 2 completa: metà "reads" (Sonnet: `GET /games`, `/replay`, `DELETE`, `POST /games/import`, persistenza in `/game/analyze`) + metà stats/ELO simulato (Opus: `GET /stats/summary`, `GET /stats/progress`, algoritmo documentato in `docs/growth-analytics.md`). 68/68 test verdi. |
| `feature/history-growth-ui` | ✅ **Merged in `main`** (PR #9) | Fase 3 frontend completa: pannello Storico (lista/filtri/replay/delete/import PGN) + dashboard Crescita (grafici ELO simulato/accuracy via `buildTrendChartSvg()`, stesso stile di `buildEvalChartSvg()`). 68/68 test backend verdi, verificato live via jsdom (no browser disponibile in sandbox). |
| `feature/training-backend` | ✅ **Merged in `main`** (PR #10) | Fase 4 backend completa: `GET/POST /training/puzzles/*` (SRS SM-2 semplificato), `GET /training/weaknesses` (fase/tema), `GET /training/endgames` + `start` (drill finali, `start_fen`). Design in `docs/training-mode.md`. 93/93 test verdi. Fix collaterale: `_create_new_game` ora deriva il turno iniziale da `board.turn` invece di assumere sempre la posizione standard (necessario per i drill con `start_fen` custom). |

| `feature/training-ui` | ✅ **Merged in `main`** (PR #12) | Fase 5 frontend (ultima fase) completa: pannello Allenamento — puzzle solver SRS, dashboard debolezze, selezione drill finali. 93/93 test verdi, verificato live via jsdom su backend isolato (porta 8766, DB scratch, senza toccare il dev server dell'utente). |
| `docs/en-passant-bug-analysis` | ✅ **Merged in `main`** (PR #11) | Documento di analisi tecnica standalone per Bug #7 (`docs/en-passant-bug7-deepdive.md`) — non è un bug, verifica indipendente con python-chess, nessuna modifica al codice. Fuori dall'iniziativa a 5 fasi. |
| `docs/threatened-pieces-design` | ✅ **Merged in `main`** (PR #13) | Valutazione design completa (`docs/threatened-pieces-design.md`, nessuna implementazione): definizione = pezzo attaccato e indifeso (hanging), nuovo endpoint leggero `GET /game/{id}/threats` senza Stockfish, glow inset rosso di contorno. SEE e minacce prospettiche flaggate come v2. |
| `docs/project-state-review` | ✅ Pushed, **pronto per PR** | Valutazione generale dello stato del progetto post-Fase 5 (`docs/project-state-review.md`): punti di forza (write-through cache, `buildBoardEl()` condiviso), debito tecnico (due "epoche" di stile nel frontend, `fetchJson()` vs raw-fetch divergenti), **3 bug non tracciati trovati e ora documentati come Bug #8/#9/(FEN mancante nel drill `rook_pawn_win`)**, gap di test coverage (nessun test incrocia `start_fen`×analyze, SRS review-queue, promozione). Miglioramento rapido consigliato: fix Bug #8+#9 (~1 ora). Investimento strutturale consigliato: split di `index.html` in file `<script src>` separati + commit dell'harness jsdom riusato 3 volte e mai salvato. |

## Prossimi passi, in ordine

**L'iniziativa persistenza + storia + allenamento a 5 fasi è conclusa** (tutte le PR mergiate: #7, #8, #9, #10, #12, #13). Bug #8/#9 fixati (vedi sopra), branch `fix/analyze-start-fen` pronto per PR.

1. L'utente apre la PR per `fix/analyze-start-fen` (Bug #8/#9) e per `docs/project-state-review` (solo documentazione).
2. Verificare anche la nota del review sul FEN mancante nel drill `rook_pawn_win` (`GET /training/endgames`) — bug minore, non ancora in `docs/bugs.md`, da aggiungere se confermato.
3. In coda (non lanciate): scacchiera ridimensionabile drag-to-resize (`docs/improvements.md`); implementazione dell'overlay "pezzi in presa" (design pronto in `docs/threatened-pieces-design.md`, in attesa di via libera dell'utente); investimento strutturale sul frontend (split file + harness di test).

## Da non dimenticare

- **Nessun `Co-Authored-By` nei commit**, mai, nessuna eccezione — vale per ogni agente/branch di questo repo.
- Ogni worktree creato da `origin/<branch>` eredita l'upstream tracking di quel branch remoto: fare subito `git branch --unset-upstream` per evitare push accidentali.
- Priorità dichiarata dall'utente: dare precedenza ai task adatti a Fable quando non ci sono dipendenze bloccanti.
