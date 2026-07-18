# Chess Lab — Lezioni di teoria (analisi di design)

Data analisi: 18 luglio 2026

Estende la **Fase 4 — Allenamento mirato**. Documento di valutazione (design/opzioni/tradeoff),
non un piano di implementazione già deciso. Nessun codice scritto qui. Segue lo stesso schema di
[`docs/threatened-pieces-design.md`](threatened-pieces-design.md) e [`docs/training-mode.md`](training-mode.md).

---

## Pitch

> "Vorrei che l'area Allenamento contenesse anche delle **lezioni di teoria** — contenuti su aperture,
> tattica ed elementi di finale — arricchite da una scacchiera interattiva su cui le mosse possano
> essere **giocate dall'utente** (eseguibili) oppure **scorse come sequenza preimpostata** (auto-play di
> una linea nota, con commento), come uno *study* di Lichess o un visualizzatore PGN annotato."

Richiesta esplicita dell'utente: **valutare** l'idea e proporre un formato, non partire a implementare.
Questo documento commenta criticamente, propone un formato dati concreto con esempi reali funzionanti,
e sceglie l'approccio più coerente con i vincoli del progetto (solo-utente, locale, single-file frontend,
nessun build step, nessuna dipendenza esterna nuova).

### Perché è plausibile e dove si incastra

L'area Allenamento (Fase 4) oggi ha tre componenti, tutte **valutative/di test**: i puzzle dai propri
blunder (metti alla prova te stesso sui tuoi errori), i drill di finali (gioca una posizione canonica
contro Stockfish), il profilo debolezze (misura dove sbagli). Manca lo strato **didattico a monte**:
qualcosa che *insegni* un concetto prima di metterti alla prova. Una lezione di teoria colma esattamente
quel buco — è il "leggi/guarda come si fa" che precede il "provaci tu" dei drill e dei puzzle.

Narrativa pedagogica coerente: **lezione → drill/puzzle**. La lezione spiega la tecnica di Lucena passo
per passo (con commento su *perché* ogni mossa); il drill di finali Lucena (già in `ENDGAME_DRILLS`) ti fa
poi **giocare** quella tecnica contro l'engine. Le due cose sono complementari, non ridondanti (§3.1).

L'idea è inoltre quasi **gratis** a livello di infrastruttura: le due meccaniche interattive richieste
— "scorri una sequenza con avanti/indietro/play" e "prova tu la mossa, validata contro quella attesa" —
**esistono già** nel codice come *replay* (Storico) e *puzzle solver* (Allenamento). Una lezione è la
loro ricombinazione, non un nuovo motore UI (§2).

---

## 1. Distinzione dalle feature esistenti (chiarire prima di progettare)

Prima del formato, mettere paletti netti — l'area Allenamento ha già contenuti "a scacchiera" e la
lezione **non** deve sovrapporsi:

| Feature | Sorgente contenuto | Cosa fa l'utente | Natura |
|---------|--------------------|------------------|--------|
| **Puzzle da blunder** (Fase 4) | Auto-generata dai propri errori (`analysis_results`) | Trova l'unica mossa migliore | Test, sui *tuoi* buchi |
| **Drill di finali** (Fase 4) | Lista statica `ENDGAME_DRILLS` (16 FEN canonici) | **Gioca** la posizione contro Stockfish fino a matto/patta | Pratica, giocando |
| **Lezione di teoria** (*questa*) | Lista statica curata a mano | **Guarda** una linea narrata + prova mosse-chiave guidate | Didattica, spiegando |

Il confine col **drill di finali** è il più delicato perché entrambi toccano i finali. La differenza è
netta e va tenuta:
- Il **drill** ti mette al tratto su un FEN e ti fa **giocare tutta la sequenza** contro un avversario
  (Stockfish a piena forza come "tablebase" didattica). Nessun commento, nessuna guida: sei solo.
- La **lezione** ti mostra la **linea corretta già scelta**, con **commento** mossa-per-mossa sul *perché*,
  e ti chiede di trovare tu solo le mosse-chiave marcate (senza avversario che gioca davvero: le risposte
  sono scriptate nella linea). È il "come si fa", non il "fallo da solo".

Uso ideale in sequenza: la lezione "La tecnica del ponte di Lucena" spiega; poi un bottone rimanda al
drill `lucena` per giocarla contro l'engine. Questo *link lezione→drill* è una piccola miglioria (§6).

---

## 2. Meccaniche di board — riusare replay + puzzle, non inventare

Le due interazioni richieste esistono già entrambe. `buildBoardEl()` è il renderer condiviso (partita
live, replay, puzzle solver) e accetta già tutto il necessario: `fen`, `orientation`, `lastMove`,
`selectedSq`, `legalMoves`, `onSquare` (e `threatSquares`, dalla feature pezzi in presa). È sufficiente
così com'è — **nessuna modifica al renderer**.

### 2.1 Modalità "show" (auto-play / dimostrazione) → è il replay

Identica al **replay** dello Storico (`GET /game/{id}/replay`), che è il precedente più vicino in assoluto:
- il replay consuma un array `fens[]` precomputato e mostra `fens[idx]` con avanti/indietro/inizio/fine +
  navigazione da tastiera (←/→/Home/End) e click-to-jump sulla move-list. La board è in sola lettura
  (`onSquare` omesso in `buildBoardEl`).
- Una lezione in modalità "show" è esattamente questo, più: (a) il **commento** della mossa corrente
  mostrato accanto alla board; (b) un bottone **Play** opzionale che avanza da solo con un `setInterval`
  (uno step ogni ~1.5–2s) attraverso le mosse "show", fermandosi su una mossa "play".

Il frontend NON calcola le posizioni: la sequenza di FEN è precomputata (§4), esattamente come `fens[]`
nel replay. Si evita così di reimplementare l'applicazione delle mosse in JS — linea di design esplicita
del progetto ("la fonte di verità è sempre il backend"; `generateMoveCandidates` è solo euristica visiva).

### 2.2 Modalità "play" (trova-tu-la-mossa) → è il puzzle solver

Identica all'interazione del **puzzle solver** (`onPuzzleSquare`): click sul proprio pezzo →
`generateMoveCandidates()` mostra i candidati → click sulla destinazione → si costruisce l'UCI (con
`askPromotion` se promozione). La differenza è solo *dove* si valida:
- Il puzzle fa un `POST /training/puzzles/{id}/answer` perché la soluzione (`best_move_uci`) **non** è nel
  client finché non si risponde.
- La lezione ha la mossa attesa **già nei dati** (è una lezione, non un test cieco): la validazione è un
  **confronto UCI lato client** (`playedUci === expectedUci`, case-insensitive) — stessa semantica del
  match del puzzle, ma senza round-trip perché la risposta è nota. Nessun endpoint di risposta serve.
  - Mossa giusta → avanza, mostra il `comment`, riproduce il suono (`playSound`).
  - Mossa sbagliata → feedback gentile ("Non è la mossa della lezione, riprova") e **non** avanza. Nessuna
    penalità, nessuno scoring: è didattica, non un esame.

Non è validazione di **legalità** (quella richiederebbe l'engine): è un confronto stringa "hai trovato
*la* mossa". Coerente con il fatto che i candidati client-side sono euristici e la posizione risultante
arriva comunque dalla sequenza precomputata.

**Conclusione: zero nuovi pattern UI.** Una lezione = replay (per gli step "show") + interazione puzzle
(per gli step "play") + un pannello di commento. Tutto già esistente e riusabile.

---

## 3. Formato del contenuto — opzioni e scelta

Cosa rappresenta una "lezione": un `start_fen`, una linea principale di mosse, un commento per mossa, un
marcatore per mossa (`show` vs `play`), e metadati (titolo, categoria, intro). Tre opzioni per *dove* e
*come* vivono i dati:

| # | Opzione | Pro | Contro | Verdetto |
|---|---------|-----|--------|----------|
| A | **Lista Python in `main.py`** (come `ENDGAME_DRILLS`) | Massima coerenza col precedente | I drill sono one-liner; le lezioni hanno commenti multi-frase per mossa → `main.py` si gonfia di prosa | Divergenza giustificata: contenuto ≠ codice |
| B | **File dati statico** (JSON bundled, es. `backend/data/lessons.json`) caricato all'avvio | Separa contenuto da codice; autorabile senza toccare `.py`; niente dipendenze nuove | Un file in più | **✅ SCELTA.** Il contenuto è dati, non logica |
| C | **DB / import PGN annotato dinamico** | Estensibile, contenuto utente | Serve schema+migration+UI di authoring per zero utenti reali; over-engineering per un tool solo-locale | Scartata per v1 |

### Raccomandazione: opzione B — file dati statico curato a mano

Contenuto **statico, curato a mano**, bundled con l'app come `backend/data/lessons.json`. Motivazioni,
tutte allineate ai vincoli del progetto:
- **Coerente con `ENDGAME_DRILLS`**: il progetto già si fida di "piccolo set statico curato, nessun dataset
  esterno". Le lezioni sono la stessa filosofia. La sola differenza (file JSON invece di lista Python) è
  perché ogni lezione porta paragrafi di commento: tenerli fuori da `main.py` è più pulito.
- **Nessuna dipendenza esterna nuova**, nessun dataset da importare, nessuna chiave API — come tutta la
  Fase 4.
- **Autorabile a mano**: le lezioni le scrive un umano (o un agente Opus) direttamente in FEN + mosse +
  commento. Non c'è pipeline di import: è la stessa scelta dei drill di finali e dei FEN canonici.

Scartata l'opzione C (DB/import dinamico): non c'è alcun utente, alcun account, alcun bisogno di contenuto
generato dall'utente. Aggiungere una tabella + migration + UI di authoring per gestire ~5 lezioni statiche
è sproporzionato. Se un domani servisse contenuto importato (PGN annotati esterni), sarà una fase a sé —
non v1.

### 3.1 Espansione FEN — chi calcola la sequenza di posizioni

La modalità "show" ha bisogno di un array di FEN (uno per posizione), come `fens[]` nel replay. Due modi:

- **Precomputare i FEN a mano** dentro il JSON: fragile ed error-prone (una FEN scritta male non si accorge
  finché non la vedi rotta sulla board). Scartato.
- **Espandere lato backend** con `python-chess`: il JSON contiene solo `start_fen` + le mosse (UCI o SAN);
  un endpoint rigioca la linea in una `chess.Board` e restituisce la sequenza di FEN completa — **esattamente
  come fa `GET /game/{id}/replay`** con `moves.fen_before`. Bonus: valida che la linea autorata sia **legale**
  al caricamento (una mossa illegale nel JSON esplode subito in fase di test, non in produzione).

Questo motiva un **endpoint backend sottile** (§4): riusa `python-chess` come fonte di verità (coerente
con l'architettura) e tiene il frontend un puro stepper identico al replay. È lo stesso identico pattern
di `/replay`, applicato a contenuto statico invece che a una partita persistita.

---

## 4. Backend vs frontend puro — endpoint sottile, nessuna persistenza

Serve un backend? Analisi contro il vincolo "single-file frontend, nessun build step, tutto lo stato in
memoria o nella SQLite dello storico":

| Aspetto | Serve backend? |
|---------|----------------|
| Servire la lista lezioni + una lezione | Sì, sottile — carica il JSON e (per il dettaglio) espande i FEN via `python-chess` (§3.1). Analogo a `GET /training/endgames` + `GET /game/{id}/replay`. |
| Validare la mossa "play" | **No** — confronto UCI lato client (§2.2), la mossa attesa è nei dati della lezione |
| Tracciare i progressi / lezioni completate | **No in v1** — vedi sotto |

### Raccomandazione: due endpoint read-only, nessuna scrittura

```python
# Lista delle lezioni disponibili (metadati, senza la linea completa).
GET /training/lessons
Response: {
  "lessons": [
    { "id": "italiana-idee", "title": "L'Apertura Italiana: sviluppo e pressione su f7",
      "category": "opening", "level": "beginner",
      "summary": "Le idee di base del Giuoco Piano: centro, sviluppo e il punto debole f7." }
  ]
}

# Dettaglio di una lezione, con la sequenza di FEN già espansa (come /replay).
GET /training/lessons/{lesson_id}
Response: {
  "id": "italiana-idee",
  "title": "...", "category": "opening", "level": "beginner",
  "orientation": "white",
  "intro": "In questa lezione impari le prime mosse dell'Italiana...",
  "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "fens": ["<fen dopo 0 mosse>", "<dopo 1>", "..."],   # N+1 FEN, come replay
  "line": [
    { "ply": 1, "uci": "e2e4", "san": "e4", "mode": "show", "comment": "..." },
    { "ply": 5, "uci": "f1c4", "san": "Bc4", "mode": "play",
      "prompt": "Sviluppa l'alfiere...", "comment": "..." }
  ]
}
# 404 se lesson_id non esiste.
```

`fens[]` è calcolato dal backend rigiocando `line[].uci` da `start_fen` (validazione inclusa). Il frontend
consuma `fens[idx]` per lo stepping "show" e `line[idx].uci` come mossa attesa per gli step "play". È il
**contratto del replay** (`{fens, moves}`) più `mode`/`comment`/`prompt` — nessuna forma dati nuova da
inventare.

### Perché NIENTE persistenza dei progressi in v1

Tracciare "quali lezioni ho completato" richiederebbe una tabella + migration. Ma:
- **Zero account utente** — non c'è un "chi" a cui appartiene un progresso.
- Il valore di un checkmark "completata" per ~5 lezioni statiche è marginale su un tool solo-locale.
- Le lezioni sono **rigiocabili all'infinito** e senza costo: non c'è una coda SRS come per i puzzle, non
  c'è uno stato "scaduta". Rifarle è gratis e desiderabile.

Se si volesse un minimo di "già vista", basta un flag **in-memory di sessione** (o al più `localStorage` —
ma CLAUDE.md vieta `localStorage` *per lo stato partita*; una spunta cosmetica di lezione è un caso diverso,
comunque rimandabile). In v1: nessun tracking. Rimandato se mai servisse.

---

## 5. Esempi reali funzionanti (per dimostrare che il formato regge)

Due lezioni complete e **verificate legali**, in italiano (convenzione CLAUDE.md), che coprono i due stili:
una opening prevalentemente "show" con una mossa "play", una tattica che culmina in una mossa "play".

### Esempio A — Apertura Italiana (Giuoco Piano)

```json
{
  "id": "italiana-idee",
  "title": "L'Apertura Italiana: sviluppo e pressione su f7",
  "category": "opening",
  "level": "beginner",
  "orientation": "white",
  "summary": "Le idee di base del Giuoco Piano: occupare il centro, sviluppare i pezzi e puntare a f7.",
  "intro": "L'Italiana è una delle aperture più antiche e naturali. L'idea è semplice: prendi il centro con il pedone, sviluppa i pezzi leggeri verso il centro e punta l'alfiere sul punto più debole della posizione nera, il pedone f7 difeso solo dal re.",
  "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "line": [
    { "uci": "e2e4", "san": "e4", "mode": "show",
      "comment": "Il Bianco occupa il centro e apre le diagonali per l'alfiere di re e per la donna." },
    { "uci": "e7e5", "san": "e5", "mode": "show",
      "comment": "Il Nero risponde simmetricamente, contendendo il centro con lo stesso pedone." },
    { "uci": "g1f3", "san": "Nf3", "mode": "show",
      "comment": "Sviluppo con minaccia: il cavallo esce e attacca subito il pedone e5. Sviluppare creando una minaccia è sempre efficiente." },
    { "uci": "b8c6", "san": "Nc6", "mode": "show",
      "comment": "Il Nero difende e5 sviluppando a sua volta un cavallo verso il centro. Difesa e sviluppo in una mossa sola." },
    { "uci": "f1c4", "san": "Bc4", "mode": "play",
      "prompt": "Sviluppa l'alfiere di re nella casa più aggressiva: cerca la diagonale che punta dritta al pedone f7.",
      "comment": "Bc4! L'alfiere prende la diagonale a2-g8 e mira a f7, difeso solo dal re nero. Questa pressione su f7 è il cuore dell'Italiana." },
    { "uci": "f8c5", "san": "Bc5", "mode": "show",
      "comment": "Il Nero imita: anche il suo alfiere esce a puntare f2. Questa simmetria è il Giuoco Piano, la 'partita tranquilla'." },
    { "uci": "c2c3", "san": "c3", "mode": "show",
      "comment": "Una mossa di preparazione: il pedone in c3 apre la strada a d4, con cui il Bianco vuole costruire un grande centro di pedoni al prossimo colpo." }
  ]
}
```

Flusso: 4 mosse mostrate (idee di apertura), poi una **play** (l'utente deve trovare `Bc4`, la mossa
tematica), poi due mosse mostrate di chiusura. Se l'utente prova un'altra mossa allo step 5, feedback
gentile e nessun avanzamento.

### Esempio B — La forchetta di cavallo

```json
{
  "id": "forchetta-cavallo",
  "title": "La forchetta di cavallo: attaccare due pezzi insieme",
  "category": "tactic",
  "level": "beginner",
  "orientation": "white",
  "summary": "Il motivo tattico più redditizio per chi inizia: il cavallo attacca re e donna nello stesso momento.",
  "intro": "Il cavallo è il maestro delle forchette. Dalla casa giusta attacca due pezzi contemporaneamente, e se uno dei due è il re (scacco!) l'avversario è costretto a pararlo e non può salvare l'altro. Qui il Bianco muove e vince la donna.",
  "start_fen": "4q1k1/8/8/3N4/8/8/8/K7 w - - 0 1",
  "line": [
    { "uci": "d5f6", "san": "Nf6+", "mode": "play",
      "prompt": "Trova la forchetta: c'è una casa da cui il cavallo dà scacco al re e attacca la donna nello stesso momento.",
      "comment": "Nf6+! Il cavallo dà scacco al re in g8 e allo stesso tempo attacca la donna in e8. Il Nero deve parare lo scacco muovendo il re, e non fa in tempo a salvare la donna." },
    { "uci": "g8g7", "san": "Kg7", "mode": "show",
      "comment": "Il re è obbligato a spostarsi per uscire dallo scacco — ma così abbandona la donna al suo destino." },
    { "uci": "f6e8", "san": "Nxe8+", "mode": "show",
      "comment": "Il cavallo cattura la donna, per giunta con un altro scacco. Con una sola mossa doppia hai vinto il pezzo più forte: questa è la forza della forchetta di cavallo." }
  ]
}
```

Flusso: intro che imposta il tema, poi **subito** una play (trova `Nf6+`), la risposta forzata scriptata
del Nero (`Kg7`, mostrata), la conclusione mostrata (`Nxe8+` vince la donna). Compatta e ad alto impatto
didattico. Legalità verificata: `Nd5-f6+` dà scacco a `g8` e attacca `e8`; dopo `Kg7`, `Nf6xe8+` cattura la
donna dando di nuovo scacco.

Questi due esempi provano che il formato copre sia la lezione **narrativa** (opening, molti "show") sia
quella **interattiva** (tattica, il "play" è il fulcro), con la stessa identica struttura dati.

---

## 6. Scope v1

Modesto e di valore — è una feature da "poche ore", non una piattaforma di contenuti.

**Dentro v1:**
- File statico `backend/data/lessons.json` con **~5–6 lezioni** curate a mano, distribuite sulle tre
  categorie:
  - **2 aperture** (es. Italiana come sopra; e.g. la spinta centrale/sviluppo in una seconda apertura tipo
    Scandinava o difesa Caro-Kann semplificata).
  - **2 tattiche** (es. la forchetta di cavallo come sopra; l'inchiodatura o il matto della base — back-rank).
  - **1–2 finali "tecnica"** narrati (es. l'opposizione re+pedone, la tecnica del ponte di Lucena passo per
    passo) — distinti dai drill omonimi (§1): qui è il *commento sul perché*, il drill è il *giocarlo*.
- Backend: `GET /training/lessons` (lista) + `GET /training/lessons/{id}` (dettaglio con `fens[]` espansi
  via `python-chess`, come `/replay`). Read-only, nessuna scrittura DB.
- Frontend: sotto-sezione "Lezioni" nella tab **Allenamento** (accanto a puzzle / debolezze / drill).
  Il visualizzatore riusa `buildBoardEl()` + la meccanica di stepping del replay (avanti/indietro/play,
  frecce tastiera) + l'interazione click-click del puzzle per le mosse "play". Pannello commento accanto
  alla board, sincronizzato con lo step corrente.
- Nessun tracking progressi.

**Rimandato (post-v1):**
- **Varianti / "e se?"** (albero di mosse alternative, non solo linea principale). PGN nativo le supporta
  (`chess.pgn` legge le variations), ma raddoppiano la complessità UI (navigazione ad albero) e non servono
  per lezioni base. v1 = **solo linea principale**.
- **Più mosse accettabili** per uno step "play" (v1: una sola UCI attesa; se una posizione ne ammette due
  ugualmente buone, la si evita in fase di authoring o si sceglie quella tematica).
- **Link lezione → drill** (bottone "Prova ora contro l'engine" che apre il drill di finale corrispondente).
  Piccola miglioria ad alto valore, candidabile a v1 se il costo è basso (è solo un `POST
  /training/endgames/{id}/start` già esistente).
- **Tracking "completata"** (in-memory o cosmetico).
- **Auto-play configurabile** (velocità dello step timer): v1 una velocità fissa ragionevole.

**Fuori scope (di ogni versione):** authoring UI, import di study/PGN annotati esterni, contenuto generato
dall'utente, dataset esterni. Le lezioni restano un piccolo set statico curato, come i drill di finali.

---

## 7. Effort stimato e collocazione in roadmap

**Collocazione:** naturale come **nuova sotto-voce della Fase 4 — Allenamento** (la lezione vive nella tab
Allenamento, accanto a puzzle/debolezze/drill, e ne completa lo strato didattico "a monte"). In alternativa,
una voce out-of-roadmap in [`docs/improvements.md`](improvements.md) se la si vuole anticipare fuori dal
piano di fase. La collocazione in Fase 4 è la più coerente col contenuto.

**Stima** (stile delle righe roadmap esistenti):

| Attività | Ore stimate | Modello suggerito |
|----------|-------------|-------------------|
| Formato `lessons.json` + endpoint `GET /training/lessons` + `GET /training/lessons/{id}` (espansione FEN via python-chess, riuso pattern `/replay`) | ~1.5 ore | Sonnet |
| Authoring di 5–6 lezioni (FEN + mosse + commento in italiano) — è il costo reale, contenuto di qualità | ~2–3 ore | Opus |
| Frontend: sotto-sezione "Lezioni" nella tab Allenamento — visualizzatore che ricombina replay-stepping + interazione puzzle + pannello commento | ~2 ore | Fable (FE-heavy, ma per lo più ricombinazione di codice esistente) |

**Totale: ~5–6 ore.** Il grosso non è codice (le meccaniche esistono già) ma **scrivere buon contenuto
didattico** — quella è la parte che merita Opus.

---

## 8. Domande aperte

1. **Chi/come autora le lezioni oltre le due d'esempio?** Servono FEN corretti e commento di qualità
   scacchistica: è un compito da agente Opus con revisione umana, come per il testo dei drill.
2. **Visibilità della mossa "play" nei dati** — la mossa attesa è nel JSON servito al client, quindi
   tecnicamente "sbirciabile". Per una lezione (didattica, non esame) è accettabile: si può però evitare di
   mostrare il SAN delle mosse "play" *future* nella move-list finché non risolte, per preservare il
   "trova-tu". Decisione minore, rimandabile.
3. **Categorie e livelli** — bastano `opening`/`tactic`/`endgame` + `beginner`/`intermediate`? Sufficiente
   per v1; si estende senza migrazioni (è un file JSON).
4. **Link lezione→drill** — promuoverlo a v1? Basso costo, alto valore pedagogico (chiude il loop
   spiega→gioca). Da decidere in base al tempo.
5. **Convenzione mosse nel JSON: UCI o SAN?** UCI è più robusto (nessuna ambiguità), SAN è più leggibile per
   chi autora. Proposta: autorare in SAN (leggibile), il backend le converte in UCI e produce entrambe
   nell'espansione (`python-chess` fa entrambe le direzioni banalmente). Da confermare.
6. **Una lezione "play" ammette più mosse buone?** v1 assume soluzione unica per step (come i puzzle). Se
   emergesse il bisogno, il campo `expected` potrebbe diventare una lista di UCI accettabili — estensione
   compatibile, non necessaria ora.
