# Chess Lab — Allenamento mirato (analisi di design)

Data analisi: 7 luglio 2026 (brainstorming Opus 4.8)

---

## Concept

Fase 4 trasforma le partite già giocate (persistite in Fase 3) in materiale di allenamento attivo, invece che in semplici statistiche a consuntivo. Quattro componenti, pensate come pipeline coerente:

1. **Puzzle dai propri blunder** — ogni errore reale diventa un esercizio.
2. **Spaced repetition** — l'errore torna a intervalli crescenti finché non è "acquisito".
3. **Profilo debolezze** — aggregazione per fase di gioco e tema tattico: dice *dove* studiare, non solo *quanto* si è forti.
4. **Drill di finali teorici** — la lacuna tecnica più comune e più facilmente colmabile, indipendente dallo storico partite.

Le prime tre formano una pipeline: *rileva l'errore → ripassalo nel tempo → misura la debolezza residua*. Valgono più della somma delle parti e vanno pensate insieme.

---

## 1. Puzzle da blunder (Blunder Replay)

### Generazione

Ogni riga di `analysis_results` (Fase 3) con `classification` in `{"blunder", "mistake"}` è candidata a diventare un puzzle:

```
puzzle.fen         = FEN della posizione PRIMA della mossa sbagliata
puzzle.best_move_uci = analysis_results.best_move_uci
puzzle.source       = "blunder" | "mistake"
```

Non serve rigenerare il FEN da zero: la sequenza di FEN per partita è già prevista da `GET /game/{id}/replay` (Fase 3) — il puzzle prende il FEN al ply corrispondente, prima del push della mossa giocata.

### Perché funziona

Un errore rivisto nella propria posizione reale è didatticamente più forte di un puzzle generico da dataset esterno: è tarato esattamente sui buchi del giocatore, nel contesto (struttura pedonale, pezzi in gioco) in cui l'errore è realmente avvenuto.

### Flusso utente

1. `GET /training/puzzles/next` restituisce la prossima carta scaduta (vedi SRS sotto) o, se la coda è vuota, genera un nuovo puzzle dal blunder/mistake più recente non ancora trasformato in carta.
2. L'utente gioca una mossa sulla posizione del puzzle.
3. `POST /training/puzzles/{id}/answer` confronta `move_uci` con `best_move_uci` (match esatto, non serve tolleranza in centipawn — è un puzzle a soluzione unica, coerente con la modalità puzzle generica già pianificata in Fase 6).
4. Risposta corretta/sbagliata aggiorna la carta SRS.

---

## 2. Spaced repetition (SM-2 semplificato)

### Perché

Un errore visto una volta si dimentica. La spaced repetition è il meccanismo che trasforma "ho capito perché ho sbagliato" in "lo riconosco a colpo d'occhio in partita" — è il moltiplicatore di valore dei puzzle da blunder.

### Algoritmo

Versione semplificata di SM-2 (SuperMemo 2), sufficiente per il volume di carte di un utente singolo — non serve la formula completa con fattore di facilità continuo se aggiunge complessità sproporzionata al beneficio:

```
Stato carta: interval_days, ease_factor (default 2.5), correct_streak, due_at

Risposta CORRETTA:
  correct_streak += 1
  if correct_streak == 1: interval_days = 1
  elif correct_streak == 2: interval_days = 3
  else: interval_days = round(interval_days * ease_factor)
  ease_factor = min(ease_factor + 0.1, 3.0)
  due_at = now + interval_days giorni

Risposta SBAGLIATA:
  correct_streak = 0
  interval_days = 1
  ease_factor = max(ease_factor - 0.2, 1.3)
  due_at = now + 1 giorno
```

Questo copre la progressione 1 → 3 → ~7 → ~17 giorni per una serie di risposte corrette consecutive con `ease_factor` di partenza, che è l'ordine di grandezza (1/3/7/30) descritto nel brainstorming iniziale.

### Schema

```sql
puzzles(
  id, game_id, ply, fen, best_move_uci,
  source TEXT CHECK(source IN ('blunder','mistake')),
  created_at
)

srs_cards(
  id, puzzle_id REFERENCES puzzles(id),
  due_at, interval_days, ease_factor, correct_streak,
  last_reviewed_at
)
```

Una carta SRS viene creata al primo tentativo di un puzzle (non alla generazione) — un puzzle mai tentato non è ancora "in coda di ripasso".

---

## 3. Profilo debolezze per fase e tema

### Classificazione fase di gioco

Euristica su `ply` e conteggio pezzi, nessuna libreria esterna:

```
opening:    ply <= 20
endgame:    materiale totale su board <= soglia (es. somma valori pezzi minori di regine+torri+minori ≈ 13 punti, o meno di 12 pezzi totali)
middlegame: tutto il resto
```

### Classificazione tema tattico

Approssimazione via python-chess sulla posizione **prima** della mossa sbagliata, confrontando con la mossa migliore:
- **Fork mancato**: `best_move` porta un pezzo che attacca ≥2 pezzi avversari di valore, la mossa giocata no.
- **Pin non sfruttato/subito**: uso di `board.is_pinned()` sul pezzo coinvolto.
- **Re esposto**: `board.checkers()` dopo la mossa giocata, o riduzione di pedoni scudo attorno al re.

Sono euristiche approssimate, non un motore tattico completo — vanno presentate come "temi probabili", non come diagnosi certa. Sufficienti per orientare lo studio, non per un'etichettatura accademica.

### Endpoint

```
GET /training/weaknesses
{
  "by_phase": {
    "opening":    {"avg_loss_cp": 12.3, "count": 45},
    "middlegame": {"avg_loss_cp": 34.1, "count": 120},
    "endgame":    {"avg_loss_cp": 58.7, "count": 30}
  },
  "by_theme": {
    "fork":         {"missed_count": 8},
    "pin":          {"missed_count": 5},
    "king_safety":  {"missed_count": 12}
  }
}
```

---

## 4. Drill di finali teorici

### Perché

I finali sono la parte più insegnabile e più trascurata sotto i 1800 ELO circa, e sono deterministici: Stockfish a piena forza è già un "tablebase" sufficientemente accurato per l'uso didattico (una tablebase Syzygy reale migliorerebbe la precisione ma non è necessaria per partire).

### Set iniziale (hard-coded, ~15-20 posizioni)

Esempi da includere: K+Q vs K, K+R vs K, opposizione re-pedone, posizione di Lucena, posizione di Philidor, finale di due alfieri vs re, finale di cavallo+alfiere vs re (avanzato, opzionale). Ogni voce ha `fen`, `goal` (`"win"` o `"draw"`), `description` breve.

### Meccanica

`POST /training/endgames/{id}/start` crea una partita come `POST /game/new`, ma con posizione iniziale custom — richiede estendere `NewGameRequest` con un campo opzionale `start_fen: str | None`. Se assente, comportamento invariato (posizione standard). Il resto del flusso (mosse, game-over, PGN) riusa l'infrastruttura esistente senza modifiche.

---

## Cosa NON fare in questa fase

- Non costruire un motore tattico proprio per il riconoscimento temi — le euristiche python-chess bastano per uno strumento personale.
- Non importare il dataset Lichess puzzles qui — è una fonte diversa (puzzle generici, non self-generated) e resta pianificata separatamente in Fase 6.
- Non introdurre un vero algoritmo ELO/rating per i puzzle — il tracking è `correct_streak` + intervallo SRS, non un punteggio competitivo.

---

## Rischi e mitigazioni

| Rischio | Mitigazione |
|---------|-------------|
| Pochi blunder registrati → coda puzzle vuota | Fallback: includere anche `classification == "inaccuracy"` se non ci sono blunder/mistake sufficienti, o mostrare i drill finali come alternativa |
| Classificazione tema tattico imprecisa | Presentarla come suggerimento ("tema probabile"), non come verità assoluta; non bloccare il puzzle su questa etichetta |
| SM-2 troppo aggressivo/blando per un solo utente | Parametri (1/3/7 giorni, ease_factor 1.3-3.0) sono standard e regolabili in un secondo momento senza cambiare lo schema |

---

## Dipendenze

- Fase 3 completata (`analysis_results`, `GET /game/{id}/replay`).
- Nessuna dipendenza esterna nuova (niente dataset, niente librerie, niente chiavi API).
