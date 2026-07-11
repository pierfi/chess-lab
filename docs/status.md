# Chess Lab — Stato sessione / roadmap di ripresa

Documento leggero, aggiornato ad ogni pausa di sessione, per riprendere il lavoro senza dover
rileggere l'intera cronologia. Per il piano di fase completo (schema DB, endpoint, design)
vedi `CLAUDE.md` → sezione "Roadmap fasi" e la memoria di progetto
`project_chess_lab_persistence_analytics`. Questo file è solo lo **snapshot dei branch in volo**.

Ultimo aggiornamento: **11 luglio 2026** (sessione in pausa per limite di utilizzo al 90%).

**Nota di ripresa:** i due agenti background (`feature/history-growth-ui` su Fable, `feature/training-backend` su Opus) sono task asincroni gestiti dall'infrastruttura, non dalla sessione: continuano a girare anche se questa sessione si interrompe. Alla ripresa, controllare prima lo stato di questi due branch/worktree (vedi tabella sotto) prima di rilanciare qualsiasi cosa — se hanno già finito, verificarli (test, no co-author trailer, working tree pulito) e pushare prima di procedere oltre.

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

| `feature/training-ui` | ✅ Pushed, **pronto per PR** | Fase 5 frontend (ultima fase) completa: pannello Allenamento — puzzle solver SRS, dashboard debolezze, selezione drill finali. 93/93 test verdi, verificato live via jsdom su backend isolato (porta 8766, DB scratch, senza toccare il dev server dell'utente). |
| `docs/en-passant-bug-analysis` | ✅ **Merged in `main`** (PR #11) | Documento di analisi tecnica standalone per Bug #7 (`docs/en-passant-bug7-deepdive.md`) — non è un bug, verifica indipendente con python-chess, nessuna modifica al codice. Fuori dall'iniziativa a 5 fasi. |
| `docs/threatened-pieces-design` | 🔄 **In corso** (agente Opus) | Valutazione design (non implementazione) dell'idea "evidenziare pezzi in presa in Assisted Mode" — utente ha confermato: definizione = attaccato E non difeso (hanging), non attacco generico né SEE. Worktree: `.claude/worktrees/threatened-pieces-design`. |

## Prossimi passi, in ordine

1. L'utente apre la PR per `feature/training-ui` — con questa, **l'iniziativa persistenza + storia + allenamento a 5 fasi è conclusa**.
2. Attendere fine valutazione `docs/threatened-pieces-design` (Opus) — solo documento di design, nessuna implementazione da lanciare finché l'utente non decide sul da farsi.
3. In coda (non lanciata): scacchiera ridimensionabile drag-to-resize, scopata in `docs/improvements.md`.

## Da non dimenticare

- **Nessun `Co-Authored-By` nei commit**, mai, nessuna eccezione — vale per ogni agente/branch di questo repo.
- Ogni worktree creato da `origin/<branch>` eredita l'upstream tracking di quel branch remoto: fare subito `git branch --unset-upstream` per evitare push accidentali.
- Priorità dichiarata dall'utente: dare precedenza ai task adatti a Fable quando non ci sono dipendenze bloccanti.
