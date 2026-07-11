# Chess Lab — Stato sessione / roadmap di ripresa

Documento leggero, aggiornato ad ogni pausa di sessione, per riprendere il lavoro senza dover
rileggere l'intera cronologia. Per il piano di fase completo (schema DB, endpoint, design)
vedi `CLAUDE.md` → sezione "Roadmap fasi" e la memoria di progetto
`project_chess_lab_persistence_analytics`. Questo file è solo lo **snapshot dei branch in volo**.

Ultimo aggiornamento: **11 luglio 2026**.

---

## Rami attivi e stato

| Branch | Stato | Note |
|--------|-------|------|
| `fix/piece-svg-set` | ✅ **Merged in `main`** (PR #4) | Fix v3 pezzi (SVG Cburnett). Bug #6 in `docs/bugs.md` resta aperto finché l'utente non conferma visivamente su browser reale. |
| `investigate/en-passant-move10` | ✅ **Merged in `main`** (PR #5) | Segnalazione "mossa illegale" `10. cxd6` verificata come en passant legale (non bug). Conflitto con `main` su `docs/bugs.md` risolto e pushato (merge `a6ebdba`), 21/21 test verdi. |
| `feature/hint-engine-strength` | ✅ **Merged in `main`** (PR #6) | Forza regolabile dell'hint engine (Fable). `hint_elo` opzionale su `POST /game/{id}/hint`, default invariato (piena forza). 24/24 test verdi. |
| `feature/persistence-db` | ✅ **Merged in `main`** (PR #7) | Fase 1 (persistenza SQLite): schema 5 tabelle, write-through cache, `think_ms`. Base per tutte le fasi successive. |
| `feature/history-analytics-api` | ✅ **Merged in `main`** (PR #8) | Fase 2 completa: metà "reads" (Sonnet: `GET /games`, `/replay`, `DELETE`, `POST /games/import`, persistenza in `/game/analyze`) + metà stats/ELO simulato (Opus: `GET /stats/summary`, `GET /stats/progress`, algoritmo documentato in `docs/growth-analytics.md`). 68/68 test verdi. |
| `feature/history-growth-ui` | 🔄 **In corso** (agente Fable) | Fase 3 frontend: pannello Storico (lista/filtri/replay/delete/import PGN) + dashboard Crescita (grafici ELO simulato/accuracy, riusa lo stile di `buildEvalChartSvg()`). Worktree: `.claude/worktrees/history-growth-ui`. Non tocca il backend. |
| `feature/training-backend` | 🔄 **In corso** (agente Opus, in parallelo) | Fase 4 backend: `GET/POST /training/puzzles/*` (SRS SM-2 semplificato), `GET /training/weaknesses` (fase/tema), `GET /training/endgames` + `start` (drill finali, `start_fen`). Design completo in `docs/training-mode.md`. Worktree: `.claude/worktrees/training-backend`. Non tocca il frontend — nessuna sovrapposizione di file con `feature/history-growth-ui`, lanciati in parallelo perché indipendenti (dipendono solo da Fase 1+2, già mergiate). |

## Prossimi passi, in ordine

1. Attendere fine `feature/history-growth-ui` (Fable) e `feature/training-backend` (Opus) — lanciati in parallelo, verificare ciascuno in autonomia (test, no co-author trailer, working tree pulito) al completamento, poi push.
2. Dopo Fase 4: Fase 5 (`feature/training-ui`, dipende da Fase 3 **e** Fase 4 — non può partire finché entrambe non sono pronte).

## Da non dimenticare

- **Conferma visiva utente** del fix pezzi SVG (v3) — Bug #6 resta "da confermare" finché non arriva.
- **Nessun `Co-Authored-By` nei commit**, mai, nessuna eccezione — vale per ogni agente/branch di questo repo.
- Ogni worktree creato da `origin/<branch>` eredita l'upstream tracking di quel branch remoto: fare subito `git branch --unset-upstream` per evitare push accidentali.
- Priorità dichiarata dall'utente: dare precedenza ai task adatti a Fable quando non ci sono dipendenze bloccanti.
