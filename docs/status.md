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
| `investigate/en-passant-move10` | ✅ Pushed, pronto per PR | Segnalazione "mossa illegale" `10. cxd6` verificata come en passant legale (non bug). Conflitto con `main` su `docs/bugs.md` risolto e pushato (merge `a6ebdba`), 21/21 test verdi. |
| `feature/hint-engine-strength` | ✅ Pushed, pronto per PR | Forza regolabile dell'hint engine (Fable). `hint_elo` opzionale su `POST /game/{id}/hint`, default invariato (piena forza). Merge di `main` incluso, 24/24 test verdi. |
| `feature/persistence-db` | ✅ Pushed, **non ancora PR'd** | Fase 1 (persistenza SQLite): schema 5 tabelle, write-through cache, `think_ms`. Base per tutte le fasi successive. |
| `feature/history-analytics-api` | 🔄 **In corso** (agente Sonnet) | Fase 2, metà "reads": stacked su `feature/persistence-db`. In lavorazione: `GET /games`, `/replay`, `DELETE`, `POST /games/import`, persistenza risultati in `/game/analyze`. Worktree: `.claude/worktrees/history-analytics-api`. **Non toccare finché l'agente non segnala fine** — poi verificare (test, no co-author trailer, working tree pulito) e proseguire con la metà Opus (stats/ELO simulato) sullo stesso branch. |

## Prossimi passi, in ordine

1. Attendere fine agente Sonnet su `feature/history-analytics-api` → verificare in autonomia (pattern trust-but-verify) → push.
2. Dispatchare un agente Opus, **stesso branch/worktree**, per la seconda metà di Fase 2: `GET /stats/summary`, `/stats/progress` (algoritmo ELO simulato), `docs/growth-analytics.md`.
3. Una volta pronta la Fase 2 completa: Fase 3 (`feature/history-growth-ui`, frontend storico + grafici crescita) — **provare prima Fable**, priorità dichiarata dall'utente.
4. In parallelo, l'utente apre le PR su GitHub per i branch già pronti (`fix/piece-svg-set` già mergiata; `investigate/en-passant-move10`, `feature/hint-engine-strength`, `feature/persistence-db` in attesa).
5. Restano da avviare: Fase 4 (training backend, Opus, dipende da Fase 2), Fase 5 (training UI, dipende da Fase 3+4).

## Da non dimenticare

- **Conferma visiva utente** del fix pezzi SVG (v3) — Bug #6 resta "da confermare" finché non arriva.
- **Nessun `Co-Authored-By` nei commit**, mai, nessuna eccezione — vale per ogni agente/branch di questo repo.
- Ogni worktree creato da `origin/<branch>` eredita l'upstream tracking di quel branch remoto: fare subito `git branch --unset-upstream` per evitare push accidentali.
- Priorità dichiarata dall'utente: dare precedenza ai task adatti a Fable quando non ci sono dipendenze bloccanti.
