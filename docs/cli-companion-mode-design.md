# Chess Lab — Modalità CLI / Companion (analisi di design)

Data analisi: 20 luglio 2026

Documento di valutazione (design/opzioni/tradeoff), **non** un piano di implementazione già
deciso. Nessun codice scritto qui. Segue lo stesso schema di
[`docs/theory-lessons-design.md`](theory-lessons-design.md),
[`docs/training-mode.md`](training-mode.md) e
[`docs/threatened-pieces-design.md`](threatened-pieces-design.md): prosa ragionata + forme dati
concrete + sezioni esplicite di scope e domande aperte.

---

## Pitch

> "Vorrei anche avviare il BE in modalità CLI, in modo da poter chiedere consigli mentre sto
> giocando una partita su un altro sito o applicazione. Tipo avviare chess-lab tipo come
> claude-code, selezionare *effort* che corrisponderebbe a livello di Stockfish e dirgli la mossa
> dell'avversario, ricevere i suggerimenti (magari sia mosse suggerite o consigli testuali tipo 'hai
> il cavallo in presa'). Successivamente io riporto la mia mossa che non necessariamente deve essere
> quella suggerita. Anche in questo caso d'uso potrei generare un PGN o suggerimenti su errori,
> analisi partita etc."

Richiesta esplicita: **valutare** e proporre un design, non partire a implementare. In sintesi
l'utente vuole un compagno da terminale — una sessione REPL analoga a come si lancia `claude` — che
segue una partita giocata **altrove** (lichess.org, chess.com, una scacchiera fisica) e a cui si
riportano a voce/tastiera le mosse dei **due** lati, ricevendo in cambio consigli (mossa migliore +
avvisi testuali tipo "cavallo in presa"). A fine partita: PGN e analisi degli errori.

### Perché è plausibile e dove si incastra

La cosa più importante da capire subito è che **quasi tutto il necessario esiste già** nel backend,
ma assemblato per un caso d'uso diverso (giocare *contro* Stockfish). La modalità companion non è un
motore nuovo: è una **ricombinazione** di endpoint esistenti attorno a una semantica diversa —
"osservare e consigliare" invece di "giocare e rispondere". Le uniche due cose davvero nuove sono
(a) una modalità *observer* lato backend (registrare mosse riportate da entrambi i lati **senza** che
l'engine giochi mai nulla) e (b) il processo CLI stesso. Tutto il resto — hint, pezzi in presa,
PGN, analisi, persistenza, ELO→Skill — si riusa.

---

## 1. Cosa esiste già, cosa manca (mappare prima di progettare)

Inventario onesto degli attrezzi già in `backend/main.py`, per non reinventare nulla:

| Serve per… | Esiste già? | Endpoint / funzione |
|------------|-------------|---------------------|
| Mossa migliore + eval + candidate (MultiPV), senza toccare lo stato | ✅ sì | `POST /game/{id}/hint` (engine separato, `hint_elo` opzionale) |
| Avviso "pezzo in presa" testuale | ✅ sì | `GET /game/{id}/threats` (pura `python-chess`, **mai** Stockfish) |
| Costruire il PGN da una lista di mosse | ✅ sì | `_build_pgn(game)` (già onora `start_fen`, header, risultato) |
| Analisi post-partita con blunder/mistake/accuracy | ✅ sì | `POST /game/analyze` (ricostruisce la board dalle righe `moves`) |
| Registrare una partita esterna mossa-per-mossa, **senza engine** | 🟡 quasi | `POST /games/import` — ma ingerisce un PGN **già completo**, non incrementale |
| Riconoscimento apertura ECO live | ✅ sì | `_current_opening()` (già dentro `_board_to_state`) |
| Mapping *effort* → forza Stockfish | ✅ sì | `elo_to_skill_depth()` (la tabella ELO→Skill/depth) |
| Registrare una mossa **alla volta** riportata da un umano, per **entrambi** i lati, senza auto-play dell'engine | ❌ **no** | *— è il gap da colmare —* |

Il pezzo mancante è uno solo, ma è quello centrale: oggi **non esiste** un modo di aggiungere una
singola mossa umana a una partita tracciata senza che Stockfish risponda. Lo colmiamo al §2.

### 1.1 In particolare: `/threats` è già esattamente il "consiglio testuale" richiesto

L'utente cita `"hai il cavallo in presa"` come esempio di consiglio testuale. Quella feature **è già
in `main`** (`GET /game/{id}/threats`, vedi [`docs/threatened-pieces-design.md`](threatened-pieces-design.md)):
funzione pura della posizione, calcolata con `python-chess`, **mai** Stockfish. Ritorna i pezzi del
lato al tratto attaccati da almeno un avversario e non difesi da alcun proprio pezzo:

```json
{ "side": "white",
  "in_presa": [ { "square": "f6", "piece": "n", "value": 3, "attackers": ["e4","g5"] } ] }
```

La modalità companion **non deve reinventare** questo: lo consuma. Meglio ancora, c'è una proprietà
elegante che ricade gratis dalla semantica di `/threats` (§3.1): chiamandolo dopo **ogni** mossa si
ottengono alternativamente i pezzi in presa *tuoi* (quando è il tuo turno) e quelli *dell'avversario*
(quando tocca a lui) — cioè sia "attento, hai il cavallo in presa" sia "l'avversario ha lasciato la
torre".

---

## 2. Il gap architetturale — modalità *observer* (nessun auto-play)

`POST /game/move` valida la mossa del player e **poi gioca sempre la risposta di Stockfish**. La
companion mode ha bisogno dell'esatto opposto: una partita dove **entrambe** le mosse (avversario e
player) arrivano dall'esterno, e Stockfish viene interpellato **solo per consiglio** (hint / eval /
presa), **mai** per muovere nella partita tracciata. Serve una modalità "observer": append di una
mossa alla volta, nessun engine nel loop di gioco.

Architetturalmente questa è **una versione incrementale di `POST /games/import`**, non una variante di
`/game/move`. L'import già costruisce un intero record di partita mossa-per-mossa senza alcun engine
(rigioca la mainline, persiste una riga `moves` per ply con `think_ms=NULL`). La companion mode è
"lo stesso loop dell'import, ma dal vivo, una mossa alla volta invece che tutte in blocco".

### 2.1 Opzione A — flag `suppress_engine` su `/game/move`. Scartata.

Si potrebbe aggiungere a `MoveRequest` un flag tipo `observer: true` che salta l'auto-play. **Da
evitare**, per tre motivi concreti:

1. **`/game/move` è l'endpoint più intricato dell'app.** È intrecciato con: `_engine_move()` e il suo
   `sleep` cosmetico, il debito del clock time-control (`_debit_clock`), il marker `last_ready_at` per
   il think time, la bandierina simmetrica player/engine. Infilare un ramo `if observer` in mezzo a
   tutto questo aggiunge branch alla funzione più complessa del codice per una modalità che di quella
   logica **non usa quasi niente** (niente engine, niente clock in v1).
2. **La semantica è diversa alla radice.** `/game/move` significa "questa è la *mia* mossa, ora
   rispondimi". La companion mode significa "registra questa mossa, di *chiunque* sia il turno" —
   le mosse dell'avversario e le mie passano dallo **stesso** canale. È il loop dell'import, non il
   flusso "mossa-mia → risposta-engine".
3. **Precedente esplicito.** L'import dimostra che il progetto già sa costruire record di partita
   senza engine. Riusare *quel* pattern è più coerente che sovraccaricare `/game/move`.

### 2.2 Opzione B — endpoint companion dedicati (import incrementale). ✅ Scelta.

Una nuova famiglia di endpoint, un nuovo valore `games.source = "companion"`, e un loop di append che
riusa i mattoni dell'import (`board.push` + riga `moves` + `_build_pgn` write-through). **Nessuna
modifica a `/game/move`.** Il record risultante è indistinguibile, per `_build_pgn` e `/game/analyze`,
da qualsiasi altra partita — perché è costruito con la stessa identica forma dati (`move_objects` +
righe `moves`).

Nuovo valore `source` — nota di coerenza con lo schema: `games.source` è `String(16)` **senza CHECK a
runtime** (scelta deliberata di Fase 3, proprio per non rifiutare valori futuri). Aggiungere
`"companion"` non richiede **nessuna migration** — esattamente come `"endgame_drill"` e `"import"`
sono stati aggiunti senza toccare lo schema. Il default di `GET /games` e `/stats/*` resta `'play'`,
quindi le partite companion **non inquinano** né lo storico né l'ELO simulato a meno di chiederle
esplicitamente (stesso trattamento di import/drill).

#### Endpoint proposti

```python
# Crea una sessione companion. Nessun engine gioca mai in questa partita.
# player_color = il lato che sto giocando IO sul sito esterno (determina a chi
# /game/analyze attribuirà blunder/accuracy, e da che lato orientare i consigli).
# effort_elo = l'"effort" scelto dall'utente, mappato a forza Stockfish per i
# CONSIGLI (hint), NON un avversario — vedi §6. start_fen opzionale (partita
# ripresa a metà / posizione custom).
POST /game/companion/new
Body: { "player_color": "white"|"black", "effort_elo": 1500, "start_fen": null }
Response:  # stesso shape di _board_to_state + "source": "companion"
{ "game_id": "…", "fen": "…", "pgn": "…", "turn": "white",
  "is_check": false, "is_game_over": false, "result": null,
  "move_history": [], "move_history_san": [], "player_color": "white",
  "engine_elo": 1500, "source": "companion", "opening": null }

# Registra UNA mossa riportata dall'esterno, per il lato al tratto (chiunque sia).
# Accetta UCI o SAN (comodità di digitazione dal vivo). Nessun engine, nessuna
# risposta automatica. Persiste una riga moves come fa l'import (fen_before/uci/
# san/color, think_ms=NULL). 400 se la mossa è illegale o non parsabile (l'utente
# ha battuto male). side opzionale: se presente, asserisce che coincida con
# board.turn (guardia contro il riportare due mosse dello stesso colore).
POST /game/{id}/companion/move
Body: { "move": "Nf6" | "g8f6", "side": "black" | null }
Response:  # stesso shape di _board_to_state (turn ora è passato all'altro lato)

# Takeback: annulla l'ultima mossa registrata (ho battuto male la mossa
# dell'avversario). board.pop() + cancellazione della riga moves con ply massimo.
POST /game/{id}/companion/undo
Response:  # stato dopo il pop
```

I consigli **non** hanno bisogno di nuovi endpoint di scrittura: si leggono da
`GET /game/{id}/hint` e `GET /game/{id}/threats`, che funzionano su **qualsiasi** partita in cache,
companion inclusa (§3). PGN e analisi si leggono da `_build_pgn` (già nel campo `pgn` di ogni
risposta di stato) e da `POST /game/analyze` (§5), **senza modifiche**.

Il costo di implementazione è modesto perché ~90% del corpo di `companion/move` è già scritto: è il
corpo del `for move in parsed.mainline_moves()` di `import_game`, estratto in un helper condiviso e
chiamato con una mossa sola. Estrarre quel loop in `_append_reported_move(game, move)` **de-duplica**
import e companion invece di duplicare — coerente con la regola "estendi il backend, non duplicare"
(CLAUDE.md, *Vincoli & decisioni architetturali*).

---

## 3. Servire i consigli senza mutare lo stato

Due canali, entrambi già esistenti, entrambi read-only rispetto alla partita:

- **Mossa migliore + eval + candidate** → `POST /game/{id}/hint` (`multipv`, `depth`, `hint_elo`).
  Non tocca la board (usa un'istanza Stockfish separata, apre/chiude nel `with`). L'`hint_elo` si
  aggancia naturalmente all'*effort* scelto dall'utente (§6): un consiglio calibrato al proprio
  livello, non da super-GM.
- **Consiglio testuale "in presa"** → `GET /game/{id}/threats`. Pura `python-chess`, nessun engine,
  costo sub-millisecondo. È letteralmente l'esempio dell'utente ("hai il cavallo in presa").

### 3.1 La proprietà elegante: `/threats` copre già entrambi i lati, gratis

`/threats` ritorna i pezzi in presa **del lato al tratto**. Chiamandolo dopo ogni mossa registrata,
il lato al tratto si alterna, e quindi:

- Dopo che l'**avversario** ha mosso → tocca a me → `/threats` mostra i **miei** pezzi in presa
  ("attento, salva il cavallo prima di muovere").
- Dopo che **io** ho mosso → tocca all'avversario → `/threats` mostra i pezzi in presa
  **dell'avversario** ("ha lasciato la torre indifesa, puoi prenderla").

Cioè l'idea "gli avvisi dovrebbero segnalare anche i pezzi appesi dell'avversario, non solo i miei"
(vedi §10) ricade **automaticamente** dalla semantica esistente, senza codice nuovo — è sufficiente
invocare `/threats` a ogni ply e etichettare l'output con "tuoi" / "suoi" in base a `side` vs
`player_color`. Limite v1 ereditato da `/threats` (documentato nel suo design): "difeso anche una
sola volta ⇒ non in presa" — non cattura i pezzi difesi-ma-sotto-attacco-multiplo, e non è un motore
tattico. Per la v1 companion va benissimo: è un promemoria a colpo d'occhio, non un'analisi.

### 3.2 Il loop di consiglio, per ogni mossa registrata

```
utente riporta la mossa dell'avversario
  → POST /game/{id}/companion/move
  → GET  /game/{id}/threats      (i MIEI pezzi in presa ora)
  → hint (mossa migliore + eval) ← dal motore locale a bassa latenza (§4)
  → la CLI mostra: eval, best move (SAN), righe candidate, avvisi "in presa"
utente sceglie e gioca sul sito esterno una mossa (NON per forza quella suggerita)
utente riporta la PROPRIA mossa
  → POST /game/{id}/companion/move
  → GET  /game/{id}/threats      (i pezzi in presa dell'AVVERSARIO ora)
  → (opzionale) delta di eval rispetto al best: "hai perso 140cp, era meglio Nf6"
```

Da notare il vincolo esplicito della richiesta: **la mia mossa non è per forza quella suggerita**.
La companion mode non forza mai nulla — registra ciò che è successo davvero sul sito esterno. Il
delta eval fra la mossa suggerita e quella effettivamente giocata è il seme del "suggerimenti su
errori" live (una versione leggera dell'analisi, senza aspettare fine partita).

---

## 4. Architettura del client — thin, standalone o hybrid?

Questa è la seconda decisione di design importante. Tre opzioni:

| # | Architettura | Pro | Contro |
|---|--------------|-----|--------|
| A | **Thin client HTTP**: la CLI fa solo chiamate REST al backend su `localhost:8765` | Riuso totale (persistenza, PGN, analyze, threats, ECO); zero logica duplicata | Ogni consiglio `/hint` paga il costo di `popen_uci` + init dell'engine ad ogni chiamata (~1-2s a depth 16, MultiPV=3) — pesante in un loop dal vivo dove si digita in fretta |
| B | **Standalone**: script con `python-chess` + Stockfish locale, nessun backend | Latenza minima, offline totale | Duplica board/legalità/threats/analyze/PGN/ECO che **già esistono** server-side — contro "non duplicare" |
| C | **Hybrid**: backend come *sistema di record* (partita companion persistita, PGN/analyze/threats/stats riusati) **+** un Stockfish **locale long-lived** nella CLI per il solo loop di consiglio a bassa latenza | Latenza minima dove conta; riuso dove conta; una sola cosa nuova (l'engine locale) | Un processo engine in più da gestire (avvio/chiusura); una deroga esplicita a un vincolo (sotto) |

### Raccomandazione: **opzione C (hybrid)**, con una linea di taglio precisa

La linea di divisione è **latenza-sensibilità × costo-di-duplicazione**:

- **Ricerca engine per la mossa migliore** — latenza-sensibile *e* comunque unico pezzo che
  giustificherebbe da solo un engine locale → **Stockfish locale long-lived** nella CLI. Un solo
  processo aperto all'avvio della sessione, riconfigurato con lo *Skill Level* dell'effort scelto, e
  interrogato con `engine.analyse(board, Limit(depth=…), multipv=…)` ad ogni mossa. Niente `popen`
  per-chiamata: il consiglio arriva in una frazione del tempo di un `/hint` HTTP.
- **Tutto il resto** — non latenza-sensibile e con logica server sostanziale da riusare (persistenza,
  convenzioni header PGN, pipeline `/game/analyze`, aggregazioni `/stats`, lookup ECO) →
  **backend via REST**. La CLI mirrora la partita al server con gli endpoint companion del §2 (record
  durevole, ripresa, PGN, analisi), e legge `/threats` dal server (costo sub-ms, nessun engine
  spawn: tenerlo server-side onora "non duplicare" e riusa la definizione curata di "in presa").

In pratica la CLI mantiene una `chess.Board` locale (per applicare le mosse all'istante e alimentare
l'engine locale) **e** rispecchia ogni mossa al backend (per il record). Il mirroring può essere
best-effort/asincrono: è pura registrazione, non è nel cammino critico del consiglio. Se il backend
è irraggiungibile, la CLI degrada a "consigli sì, PGN/analisi/persistenza no" invece di rompersi.

### 4.1 L'engine locale è una deroga esplicita (e legittima) a "un'istanza per chiamata"

CLAUDE.md impone: *"Un'istanza Stockfish per chiamata API (apertura/chiusura nel `with`). Non tenere
engine in memoria globale per evitare race condition."* Un engine long-lived nella CLI **sembra**
violarlo — ma la ratio del vincolo è **la concorrenza lato server**: più richieste API che girano
nel threadpool FastAPI non devono condividere un engine globale, o si corrompono a vicenda. La CLI è
un **processo separato, single-user, single-thread**: non c'è concorrenza da cui proteggersi. Un
engine per la durata di una sessione companion in un processo sequenziale non ha alcuna race — il
vincolo semplicemente **non si applica** a quel contesto.

Va quindi documentato come **deroga esplicita e circoscritta**, non come rottura del vincolo: "il
backend continua ad aprire/chiudere un engine per chiamata; la **CLI** — processo distinto — tiene un
suo engine locale per la durata della sessione, legittimo perché sequenziale". È lo stesso spirito con
cui la Fase 2 ha aggiunto un *secondo* engine (l'hint engine) senza violare "un'istanza per chiamata":
istanze separate, isolate, ciascuna non condivisa fra thread concorrenti.

---

## 5. PGN e analisi post-partita — riuso puro, zero modifiche

Poiché la partita companion è costruita con la **stessa forma dati** di ogni altra (`move_objects` +
righe `moves` con `fen_before`), i due flussi finali funzionano **senza toccare nulla**:

- **PGN** — `_build_pgn(game)` costruisce il PGN da `move_objects` (+ `start_fen` + header + risultato).
  È già nel campo `pgn` di ogni risposta di stato companion. Nessuna modifica. La CLI espone un
  comando `/pgn` che lo scrive su file — stesso identico output dell'export frontend di Fase 3.
  - *Header:* `_build_pgn` etichetta `White`/`Black` come `"Player"`/`"Stockfish"` in base a
    `player_color`. Per una companion l'avversario non è Stockfish ma un umano esterno: un piccolo
    ritocco opzionale potrebbe rendere l'etichetta configurabile (es. `"Opponent"`), ma è cosmetico e
    rimandabile — il PGN resta valido e importabile ovunque anche così.
- **Analisi** — `POST /game/analyze` ricostruisce la board dalle righe `moves` e classifica ogni ply
  (blunder/mistake/accuracy) attribuendoli a `player_color`. Per una companion, `player_color` è il
  lato che gioco io sul sito esterno → l'analisi valuta **le mie** mosse, esattamente come voluto.
  La persistenza additiva in `analysis_results` funziona (la riga `games` companion esiste). Nessuna
  modifica: `/game/analyze` non sa né gli importa come sono nate le mosse.

Quindi "generare un PGN o suggerimenti su errori, analisi partita" (la coda della richiesta utente) è
**già interamente coperto** dagli endpoint esistenti applicati a un record companion.

---

## 6. *Effort* → forza Stockfish

L'utente vuole "selezionare *effort* che corrisponderebbe a livello di Stockfish". Si riusa **la
stessa scala già documentata** in CLAUDE.md e implementata in `elo_to_skill_depth()`:

```
ELO < 800   → Skill 0,  depth 1        ELO < 1600  → Skill 12, depth 9
ELO < 1000  → Skill 3,  depth 3        ELO < 1800  → Skill 15, depth 12
ELO < 1200  → Skill 6,  depth 5        ELO < 2000  → Skill 18, depth 15
ELO < 1400  → Skill 9,  depth 7        ELO >= 2000 → Skill 20, depth 20
```

L'*effort* è un'etichetta amichevole (es. "Principiante / Club / Esperto / Massimo") sopra un valore
ELO, esattamente come i preset time-control di Fase 6 sono etichette sopra `initial+increment`. Due
usi distinti dello stesso numero, da tenere separati:

- **Forza del consiglio** (`hint_elo` / Skill dell'engine locale): quanto forte è il coach. Un
  giocatore da 1200 può volere consigli calibrati a ~1200 (mosse che capirebbe e troverebbe) invece
  che da Stockfish a piena forza — è esattamente la ratio della "forza regolabile dell'hint engine"
  già in `docs/improvements.md`. **Default consigliato: piena forza** (effort omesso ⇒ nessuno Skill
  configurato), coerente con il default storico di `/hint`; l'utente abbassa l'effort se vuole
  consigli "a livello".
- **Profondità di ricerca** (`depth`): governa la latenza. Nella CLI conviene disaccoppiarli — depth
  fissa e ragionevole (es. 14-16) per un consiglio rapido, e Skill separato per la forza — invece di
  legare depth all'effort come fa la tabella `elo_to_skill_depth` (pensata per la *forza di gioco*,
  non per la reattività di un consiglio dal vivo). Da confermare in implementazione.

---

## 7. UX terminale — `rich` come dipendenza consigliata

Confermato con l'utente: le sequenze di escape ANSI (spinner, barre di progresso, pannelli che si
aggiornano dal vivo, come la UI di Claude Code "Compacting… (2m 11s · ↑ 3.7k tokens)") **non** sono
specifiche di Node.js — sono semplici sequenze di controllo su stdout. L'equivalente naturale in
Python è la libreria **`rich`**: spinner durante la ricerca dell'engine, pannelli di eval/mossa
migliore aggiornati in place, liste di mosse stilizzate, evidenziazione a colori dei pezzi in presa,
tabelle per le righe candidate. Sarebbe **una nuova dipendenza** da aggiungere a
`chess_app/requirements.txt` (`rich`), l'unica introdotta da questa feature — giustificata perché la
CLI *è* la feature, e una CLI curata è metà del suo valore. È una nota UX, non architettura: `rich`
non entra nel backend, vive solo nel processo CLI.

---

## 8. Scope v1

**Dentro v1:**
- Backend: `source="companion"` (nessuna migration) + `POST /game/companion/new`,
  `POST /game/{id}/companion/move` (append di una mossa umana, UCI **o** SAN, nessun engine),
  `POST /game/{id}/companion/undo` (takeback — il mis-typing dal vivo è frequente). Loop di append
  estratto in un helper condiviso con `/games/import` (de-duplica, non duplica).
- Consigli riusati as-is: `GET /game/{id}/hint`, `GET /game/{id}/threats`.
- PGN e analisi riusati as-is: campo `pgn` + `POST /game/analyze`.
- CLI (nuovo processo, hybrid §4): REPL stile `claude`; selezione effort → Skill; Stockfish locale
  long-lived per i consigli; mirroring della partita al backend; comandi `/pgn`, `/analyze`, `/undo`,
  `/hint` (richiama un consiglio on-demand), `/quit`. UI con `rich`.
- Consiglio testuale = `/threats` etichettato "tuoi / suoi" (§3.1) + best move SAN + eval + delta
  rispetto alla mossa giocata.

**Rimandato (post-v1):** vedi §10.

**Fuori scope (di ogni versione):** far giocare Stockfish nella partita companion (per definizione è
osservata, non giocata); scraping automatico delle mosse dal sito esterno (le mosse le riporta
l'utente — nessuna integrazione con lichess/chess.com API in v1); multi-utente/auth (resta un tool
solo-locale).

---

## 9. Effort stimato e collocazione in roadmap

**Collocazione:** è una feature nuova e trasversale (backend observer-mode + un intero nuovo processo
CLI), più vicina a una **voce di fase** che a un "miglioramento" di
[`docs/improvements.md`](improvements.md) (quel registro è per refinement di feature *esistenti*).
Naturale come nuova voce di roadmap a sé, affiancabile alla Fase 6 (UX avanzata) o alla Fase 7 in
base alla priorità. La feature richiede genuinamente **più di un task**, da cui la tabella:

| Settimana | Attività | Ore stimate | Modello suggerito | Stato |
|-----------|----------|-------------|-------------------|-------|
| — | Backend observer-mode: `source="companion"`, endpoint `companion/new` + `companion/move` + `companion/undo`, estrazione del loop di append condiviso con `/games/import` (de-dup) | ~3 ore | Opus | 🔲 |
| — | CLI: scheletro REPL, selezione effort→Skill, Stockfish locale long-lived, client di mirroring verso il backend, loop di consiglio (best move + eval + `/threats`) con la UX della mossa divergente (registro ciò che è stato giocato, non ciò che era suggerito) | ~4 ore | Opus | 🔲 |
| — | CLI: comandi `/pgn` e `/analyze` (mirror di endpoint esistenti) + riepilogo errori a fine partita | ~2 ore | Sonnet | 🔲 |
| — | UI `rich`: spinner ricerca, pannelli eval/mossa migliore aggiornati in place, lista mosse stilizzata, evidenza "in presa"; aggiunta di `rich` a `requirements.txt` | ~2 ore | Sonnet | 🔲 |

**Totale: ~11 ore.** Split di modello secondo la convenzione del progetto (Opus sui pezzi di
ragionamento — semantica observer-mode e UX del loop a mossa divergente; Sonnet sui task
templated/meccanici — comandi che rispecchiano endpoint esistenti e rifinitura UI una volta fissato il
pattern di interazione).

---

## 10. Idee emerse (backlog, **non** scope impegnato)

Idee genuinamente affiorate ragionando sulla feature. **Nessuna è promessa** — parcheggio, da
valutare solo se/quando la companion mode avrà priorità.

- **Salva/riprendi una sessione companion interrotta.** L'utente chiude la CLI a metà di una partita
  esterna lunga. Poiché il record è già persistito server-side (ogni `companion/move` scrive una riga
  `moves`), riprenderlo è quasi gratis: un `GET /game/{id}` ricostruisce la board via cache-miss
  (`_load_game_from_db`) e la CLI riparte dal `game_id`. Serve solo un comando `chess-lab --resume
  <game_id>` (o un elenco delle companion aperte). Piccolo, ad alto valore.
- **Segnalare i blunder/pezzi appesi dell'avversario, non solo i miei.** Come mostrato al §3.1, ricade
  **già** dalla semantica di `/threats` invocato ad ogni ply — quasi gratis. Vale la pena renderlo
  esplicito nella UX ("l'avversario ha lasciato la donna in presa") perché su un sito esterno cogliere
  l'errore altrui è metà del vantaggio.
- **Sessioni companion multiple/concorrenti.** Seguire più partite in parallelo (es. più tavoli di un
  torneo online). Il backend già distingue per `game_id`; la complessità è tutta nella CLI (quale
  sessione è "in focus"). Probabilmente over-engineering per un uso solo-locale, ma tecnicamente a
  basso costo lato server.
- **Metodi di input alternativi.** Oltre al riporto incrementale mossa-per-mossa: incollare una **FEN**
  direttamente (salto a una posizione arbitraria — riusa `start_fen`, utile per riprendere una partita
  già avanzata) o incollare un **PGN parziale** (bootstrap della sessione da un import, poi continuare
  dal vivo). L'input vocale (speech-to-move) è citato come possibilità ma è chiaramente fuori dal
  perimetro tecnico attuale.
- **Auto-hint con soglia (opt-in).** Invece di chiedere il consiglio ogni volta, mostrarlo
  automaticamente solo quando l'eval della mia ultima mossa peggiora oltre una soglia (es. −150cp) —
  cioè "ti avviso solo quando stai per sbagliare". È il gemello CLI dell'idea di coach proattivo di
  Fase 7, ma **non-AI** (puro delta eval), quindi realizzabile senza dipendenze esterne.
- **Timer/orologio informativo.** Se sto giocando a tempo sul sito esterno, un contatore nella CLI
  (puramente informativo, riporto io il tempo o lo stimo) — ma è duplicazione del time-control già in
  Fase 6 e probabilmente meglio lasciarlo al sito esterno. Citato per completezza, tendenzialmente da
  scartare.

---

## 11. Domande aperte

1. **`effort_elo` in `games.engine_elo`?** Companion non ha un vero avversario-engine. Riusare la
   colonna `engine_elo` (NOT NULL) per l'effort del *consiglio* è pragmatico (nessuna migration) e
   companion è comunque escluso dal `/stats` di default (source-filter), quindi non alimenta l'ELO
   simulato con un rating d'avversario finto — a differenza dell'import che usa la sentinella `0`.
   Confermare la convenzione (effort vs sentinella).
2. **Latenza dell'engine locale a effort alto.** A Skill 20 / depth alta il consiglio può costare più
   di quanto sia gradevole in un loop dal vivo. Disaccoppiare depth ed effort (§6) mitiga; da tarare
   un default di depth che sia "abbastanza forte, abbastanza rapido".
3. **Etichetta avversario nel PGN.** `_build_pgn` scrive `Black/White = "Stockfish"` per il lato non
   giocato dal player. Per una companion l'avversario è umano: rendere l'etichetta configurabile
   (`"Opponent"`) o accettare l'inesattezza cosmetica? Rimandabile — il PGN resta valido comunque.
4. **UCI vs SAN nell'input.** Accettare entrambi è comodo (`python-chess` fa `parse_san` e
   `Move.from_uci`), ma il SAN è ambiguo se battuto male dal vivo (es. `Nd2` con due cavalli). La
   guardia `side` + un feedback chiaro di "mossa illegale/ambigua, ribattila" copre il caso; da
   decidere se preferire UCI come formato canonico e SAN come comodità.
5. **Mirroring sincrono o best-effort?** Se il backend rallenta, il consiglio (engine locale) non deve
   aspettarlo. Proposta: mirroring asincrono/fire-and-forget con una coda locale che si risincronizza,
   così il loop di consiglio non è mai bloccato dal record durevole. Da confermare in implementazione.
6. **Dove vive il file CLI?** Un nuovo `chess_app/cli/` o un singolo modulo `chess_app/companion.py`?
   E come si lancia — `python -m chess_app.companion`, uno script console entry-point, o un flag su un
   launcher condiviso col backend? Decisione di packaging, minore, da fissare all'avvio del lavoro.
