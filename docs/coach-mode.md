# Chess Lab — Coach Mode (analisi di design)

Data analisi: 16 aprile 2026

---

## Concept

Modalità "insegnamento" in cui l'utente gioca contro Stockfish con Claude come coach in tempo reale.
Claude osserva la partita, risponde a domande e (opzionalmente) interviene proattivamente dopo errori significativi.

---

## Sub-modalità

### 1. On-demand (v1)
L'utente clicca "Ask Coach" quando vuole. Claude riceve la posizione corrente e risponde con un hint posizionale senza rivelare la mossa migliore.

### 2. Proactive (v2, opt-in)
Dopo ogni mossa dell'utente, il backend valuta il centipawn loss. Se supera una soglia (>80cp), Claude interviene automaticamente con un nudge. Per evitare che il pattern "coach parla = hai sbagliato" diventi uno spoiler implicito, il coach interviene anche su mosse buone con rinforzo positivo.

---

## Prompt design

### System prompt — struttura a tre livelli

**Livello 1 — Ruolo e vincoli:**
```
You are a chess coach. The student is playing a live game against Stockfish.
NEVER reveal the best move directly. NEVER give the move in any notation (SAN, UCI, coordinate).
Instead, guide attention to the relevant area of the board using concepts:
open files, weak squares, piece activity, king safety, pawn structure.
```

**Livello 2 — Calibrazione per ELO:**
```
Student ELO: {elo}

- Under 800: use very simple language. Focus on material and basic tactics
  (forks, pins). Ignore positional nuances.
- 800-1200: introduce positional ideas (center control, development, king safety)
  but keep it concrete ("your knight doesn't control any important squares").
- 1200-1600: discuss plans, pawn structure, piece coordination.
  Be more Socratic — ask questions instead of stating.
- 1600+: assume they see tactics. Focus on strategic subtlety,
  prophylaxis, long-term plans. Be terse.
```

**Livello 3 — Contesto per chiamata (user message):**
```
FEN: {fen}
Last move played: {san} (by {color})
Eval: {score_cp}cp — Classification: {classification}
Move history: {last 6-8 moves SAN}
```

### Decisione critica: non passare il best move

Se il best move UCI viene incluso nel prompt, Claude potrebbe rivelarlo nonostante le istruzioni. Il backend deve preprocessare il best move in metadata astratti:
- Area della board coinvolta (kingside/queenside/center)
- Tema tattico (fork, pin, discovery, skewer)
- Pezzo chiave (senza dire dove muoverlo)

Questo dà a Claude abbastanza contesto per un hint utile senza la tentazione di spoilerare.

---

## UX tradeoffs

| Aspetto | On-demand | Proactive |
|---------|-----------|-----------|
| Rispetto del flow | Alto — l'utente decide quando chiedere | Basso — interruzione non richiesta |
| Valore didattico | Medio — richiede che l'utente sappia di aver bisogno d'aiuto | Alto — cattura il "teachable moment" |
| Spoiler implicito | Nessuno | Alto se interviene solo sui blunder |
| Costo API | Basso (~5 chiamate/partita) | Medio (~20 chiamate/partita) |
| Complessità implementazione | Bassa | Media (soglie, frequency cap, hint history) |

**Raccomandazione:** partire con on-demand (v1). Aggiungere proactive come opt-in (v2) con queste mitigazioni:
- Interventi anche su mosse buone (non solo errori)
- Max 1 hint ogni 3 mosse
- Frequenza decrescente dopo l'apertura
- Toggle a tre stati: Off / On-demand / Active

---

## Costi e latenza

Modello consigliato: **Claude Haiku** (sufficiente per hint brevi, 10x più veloce di Opus).

| Scenario | Input tokens | Output tokens | Costo stimato | Latenza |
|----------|-------------|---------------|---------------|---------|
| Singola chiamata | ~400 | ~100 | ~$0.00008 | 0.5-1s |
| Partita 40 mosse, on-demand (5 hint) | ~2000 | ~500 | ~$0.0004 | |
| Partita 40 mosse, proactive (20 hint) | ~8000 | ~2000 | ~$0.0016 | |

**Ottimizzazioni:**
1. **Prompt caching** — il system prompt (~300 token) è identico per tutta la partita. Con caching Anthropic, si paga una volta.
2. **Contesto corto** — ultime 6-8 mosse SAN, non tutta la partita. Il FEN contiene già la posizione completa.
3. **Max tokens = 150** — hint lungo = hint cattivo. Forza la brevità.
4. **Parallelismo** — la chiamata Claude parte in parallelo alla mossa Stockfish. Latenza aggiuntiva percepita: zero.
5. **Rate limit** — max 20 chiamate coach per partita. Contatore visibile nella UI.

---

## Rischi e mitigazioni

| Rischio | Gravità | Mitigazione |
|---------|---------|-------------|
| Claude rivela la mossa migliore | Alta | Non passare best move nel prompt. Preprocessare in tema/area lato backend. |
| Hint sbagliato (Claude non è un GM) | Media | Per ELO <1600 raramente rilevante. Per livelli alti, limitare a osservazioni posizionali. |
| Hint ripetitivi | Media | Includere gli ultimi 2-3 hint nel contesto per evitare ripetizioni. |
| Interruzione del flow (proactive) | Alta | Default off. Toggle chiaro. Max 1 hint ogni 3 mosse. |
| Latenza percepita >2s | Media | Haiku + prompt caching + parallelismo. Mostrare mossa engine subito, hint con animazione soft dopo. |

---

## Piano di implementazione progressivo

1. **v1 (on-demand):** pulsante "Ask Coach" nella UI, chiamata API Claude con FEN + ultime mosse, risposta in un pannello chat laterale. Backend: nuovo endpoint `POST /game/{id}/coach`.
2. **v2 (proactive opt-in):** eval automatica post-mossa, soglia centipawn loss configurabile, hint history nel prompt, toggle nella UI.
3. **v3 (coach con memoria):** dopo Fase 5 (statistiche) e Fase 4 (pattern di errore ricorrenti), il coach accede allo storico partite e personalizza i consigli ("nelle ultime partite sbagli spesso i finali torre+pedoni").

---

## Endpoint previsti

```python
# Richiedi hint al coach
POST /game/{id}/coach
Body: { "question": "optional free-text question" }
Response: { "hint": "...", "hints_remaining": 17 }

# Configura coach mode
POST /game/{id}/coach/config
Body: { "mode": "off" | "on_demand" | "proactive", "proactive_threshold_cp": 80 }
```

---

## Dipendenze nuove

- `anthropic` — SDK Python per Claude API
- Chiave API Anthropic (`ANTHROPIC_API_KEY` in env)
