# Chess Lab — Statistiche di crescita & ELO simulato (spec di design)

Data analisi: 11 luglio 2026 (Opus 4.8, branch `feature/history-analytics-api`)

Questo documento è la spec autoritativa dei due endpoint di aggregazione dello
storico (`GET /stats/summary`, `GET /stats/progress`) e dell'algoritmo di **ELO
simulato**. Il frontend del grafico di crescita (Fase 3, non ancora fatto) va
costruito contro le forme di risposta qui descritte.

---

## Problema

Dopo la persistenza (Fase 3) lo storico partite esiste ma è consultabile solo
partita-per-partita (`GET /games`). Manca una vista *aggregata* che risponda a
due domande dell'utente:

1. "Come sto andando in generale?" — numeri di sintesi (partite, win rate,
   accuracy media, errori totali). → `GET /stats/summary`.
2. "Sto migliorando nel tempo?" — una trend line, non un singolo numero. →
   `GET /stats/progress`.

La (2) è la parte concettualmente difficile: **non esiste un rating reale**.
L'utente gioca solo contro Stockfish a un `engine_elo` configurato, non contro
avversari con rating in un pool. Serve un proxy direzionale, dichiaratamente non
rigoroso, che dica "la curva sale/scende" senza pretendere di essere un ELO FIDE.

### Vincoli (da CLAUDE.md)

- Endpoint read-only, nessuna modifica di schema. Query dal DB (non dalla cache
  in-memory), ORM SQLAlchemy, niente raw SQL. Endpoint `def` sincroni.
- Convenzione win/loss/draw **relativa a `player_color`** — identica a
  `GET /games?result=` (fonte unica: `_result_predicate` / `_player_result` in
  `main.py`).

---

## ELO simulato — algoritmo

Scelta: **update Elo classico, partita per partita**, con `engine_elo` come
rating dell'avversario e l'esito (relativo a `player_color`) come risultato.

```
seed  = 1200          # rating iniziale (SIM_ELO_SEED)
K     = 32            # fattore K fisso (SIM_ELO_K)

per ogni partita decisa, in ordine cronologico (created_at asc):
    score    = 1.0 (win) | 0.5 (draw) | 0.0 (loss)      # relativo a player_color
    expected = 1 / (1 + 10^((engine_elo - rating) / 400))
    rating   = rating + K * (score - expected)

simulated_elo della partita = rating DOPO l'update  (arrotondato a intero)
```

### Perché questa formula

- **È la formula Elo standard.** L'avversario ha già un "rating" naturale e
  onesto: l'`engine_elo` a cui è stato configurato Stockfish. Battere un engine a
  1800 conta più che batterne uno a 800, e la formula lo cattura da sola tramite
  il punteggio atteso. Nessun'altra euristica serve.
- **K fisso (32).** Un K adattivo (che cala con l'esperienza) è overkill per un
  singolo utente locale con poche decine di partite; 32 è il valore classico per
  giocatori non consolidati e dà una curva reattiva ma non nervosa.
- **Seed 1200.** Convenzionale (rating "principiante-intermedio" tipico). È solo
  il punto di partenza della curva: conta la *pendenza*, non il valore assoluto.
- **Direzionale, non assoluto.** Ripetuto nel nome e nella risposta: è una trend
  line. Due utenti con lo stesso `simulated_elo` non sono confrontabili — la
  metrica ha senso solo *nel tempo per lo stesso utente*.

### Perché per-partita (non per-giorno)

L'Elo è intrinsecamente un aggiornamento *per evento valutato*: un punto per
partita è la traiettoria naturale del rating. Aggregare per giorno costringerebbe
a scegliere come combinare più partite nello stesso giorno (media? ultima?),
nasconderebbe risoluzione, e non semplificherebbe nulla lato frontend (che vuole
comunque una serie di punti su un asse temporale). Ogni punto porta il campo
`date` (ISO di `created_at`), quindi il frontend può plottare su asse-tempo o su
asse-indice (`game_number`) a piacere.

### Cosa viene escluso dalla serie

- **Partite in corso** (`result` nullo): nessun esito, saltate.
- **Import** (`source='import'`): esclusi dal default `source='play'`. Fondamentale
  perché gli import hanno `engine_elo=0` (sentinella "avversario sconosciuto"):
  un avversario a 0 darebbe `expected≈0` e gonfierebbe il rating a ogni "vittoria"
  — dato privo di senso. Il filtro source li tiene fuori a monte.
- **Drill di finali** (`source='endgame_drill'`, Fase 4): esclusi dallo stesso
  default; non sono partite competitive rappresentative della forza generale.

---

## `GET /stats/summary`

Numeri headline su tutto lo storico filtrabile.

### Query params

| Param       | Default | Note |
|-------------|---------|------|
| `color`     | —       | `white`\|`black`; filtra per `player_color` |
| `source`    | `play`  | come `GET /games`; `import`/`endgame_drill` esclusi di default |
| `date_from` | —       | `YYYY-MM-DD`, inclusivo, su `created_at` |
| `date_to`   | —       | `YYYY-MM-DD`, **inclusivo del giorno intero** (internamente end-exclusive: `< date_to + 1 giorno`) |

`400` se una data non è nel formato `YYYY-MM-DD`.

### Response

```json
{
  "total_games": 42,
  "decided_games": 40,
  "analyzed_games": 30,
  "wins": 22,
  "losses": 15,
  "draws": 3,
  "win_rate": 0.55,
  "loss_rate": 0.375,
  "draw_rate": 0.075,
  "avg_accuracy": 76.4,
  "total_blunders": 18,
  "total_mistakes": 41,
  "total_inaccuracies": 63,
  "avg_think_ms_per_move": 4200
}
```

### Semantica non ovvia

- **I tassi sono relativi alle partite DECISE** (`decided_games = wins+losses+draws`),
  non a `total_games`: le partite in corso non hanno esito e non devono diluire il
  denominatore. `win_rate + loss_rate + draw_rate == 1.0` (a meno di arrotondamento)
  quando ci sono partite decise; tutti 0 quando non ce ne sono.
- **`avg_accuracy` media solo le partite ANALIZZATE** (`analyzed_at IS NOT NULL`):
  una partita non analizzata non ha accuracy e va **esclusa**, non contata come 0
  (falserebbe la media verso il basso). `null` se nessuna partita è analizzata.
- **`total_blunders/mistakes/inaccuracies`** sommano solo sulle partite analizzate
  (le colonne di riepilogo sono `NULL` altrove).
- **`avg_think_ms_per_move`** è sulle sole mosse DEL PLAYER (`moves.color ==
  games.player_color`) con `think_ms` non nullo — riflette la riflessione
  dell'utente, non quella dell'engine né il padding cosmetico (mai persistito,
  vedi Fase 3). `null` se non c'è alcun dato di timing.

---

## `GET /stats/progress`

Serie temporale + ELO simulato per il grafico di crescita.

### Query params

Identici a `/stats/summary` (`color`, `source`, `date_from`, `date_to`, stessa
semantica e stessa validazione data).

### Response

```json
{
  "seed_elo": 1200,
  "k_factor": 32,
  "games_counted": 40,
  "current_elo": 1287,
  "peak_elo": 1310,
  "series": [
    {
      "game_id": "6f0610a7",
      "date": "2026-07-11T10:00:00",
      "game_number": 1,
      "engine_elo": 1000,
      "result": "win",
      "score": 1.0,
      "simulated_elo": 1214,
      "accuracy": 78.5
    }
  ],
  "recent": {
    "window": 10,
    "games": 10,
    "elo_change": 45,
    "avg_accuracy": 74.2,
    "wins": 6,
    "losses": 3,
    "draws": 1
  }
}
```

### Semantica non ovvia

- **`series`** è in ordine cronologico (`created_at asc`, tie-break su `id` per
  determinismo). Un elemento per partita **decisa**; `game_number` è 1-based sulle
  sole partite contate (non è il ply, né l'indice globale).
- **`simulated_elo` è il rating DOPO** l'applicazione di quella partita. Il primo
  punto mostra quindi già il seed aggiornato dalla partita 1, non il seed nudo.
- **`current_elo`** = `simulated_elo` dell'ultima partita, oppure `seed_elo` se la
  serie è vuota. **`peak_elo`** = massimo raggiunto (≥ seed).
- **`accuracy`** per-partita è `games.player_accuracy` (`null` se non analizzata).
- **`recent`** aggrega le ultime `window` (=10) partite della serie:
  - `elo_change` = `simulated_elo` finale − `simulated_elo` immediatamente prima
    della finestra (o `seed_elo` se la finestra copre tutta la serie). È la
    variazione di rating *nel periodo recente*, il numero che l'utente guarda.
  - `avg_accuracy` media solo le partite analizzate della finestra (`null` se
    nessuna). `wins/losses/draws` contano gli esiti nella finestra.

---

## Edge case

| Caso | Comportamento |
|------|---------------|
| Storico vuoto (nessuna partita) | `summary`: tutti i conteggi 0, tassi 0.0, `avg_accuracy`/`avg_think_ms_per_move` `null`. `progress`: `series: []`, `current_elo`/`peak_elo` = `seed_elo`, `recent.games` 0, `recent.elo_change` 0, `recent.avg_accuracy` `null`. |
| Solo partite in corso (nessun `result`) | Contano in `total_games` ma non in `decided_games`; `series` vuota, ELO = seed. |
| Nessuna partita analizzata | `avg_accuracy` `null`, errori totali 0, `series[*].accuracy` `null`. |
| Filtro date invertito (`date_from > date_to`) | Nessun errore: intervallo vuoto → risultato "storico vuoto". |
| Import / drill | Esclusi dal default `source='play'`; interrogabili esplicitamente ma l'ELO simulato su `source='import'` non è significativo (`engine_elo=0`). |
| < 10 partite decise | `recent.window` resta 10 ma `recent.games` = numero effettivo; `elo_change` parte dal seed. |

---

## Note di implementazione

- Convenzione win/loss/draw centralizzata: `_result_predicate` (SQL, per
  `GET /games` e i filtri) e `_player_result` (Python, per l'iterazione riga-per-
  riga in `/stats`). Un solo posto da toccare se la convenzione cambia.
- Filtri di query condivisi: `_game_filter_conditions` (source/color/date) e
  `_parse_date_range` (validazione + normalizzazione end-exclusive del `date_to`).
- Costanti ELO in cima al blocco stats (`SIM_ELO_SEED`, `SIM_ELO_K`,
  `SIM_ELO_RECENT_WINDOW`), `_elo_expected` isolata e testabile.
- Nessuna nuova tabella, nessuna colonna: pura lettura aggregata sullo schema
  esistente di Fase 3.
