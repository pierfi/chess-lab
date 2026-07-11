# Analisi tecnica — "Mossa illegale" `10. cxd6` (en passant)

> Approfondimento standalone del finding già chiuso in [`docs/bugs.md`](bugs.md) (Bug #7).
> Questo documento non sostituisce quella voce — la integra con il dettaglio tecnico
> completo (regola, verifica indipendente, percorso nel codice) per chi vuole capire
> *perché* non c'era nulla da correggere, non solo *che* non c'era nulla da correggere.
>
> **Nessun codice è stato modificato per produrre questo documento.** Dove più sotto si
> parla di "percorso nel codice" si intende: il codice **esistente**, letto e verificato,
> non un diff prima/dopo — non esiste un "dopo", perché non c'è stato un fix.

---

## 1. Segnalazione originale

L'utente ha giocato una partita contro Stockfish (salvata come PGN,
`chess_app/resources_and_examples/chesslab-50e3e0c5.pgn`) e, rivedendola, ha segnalato la
mossa **10. cxd6** come impossibile. In termini suoi:

- Alla mossa 9 il Nero gioca **d5** (`9...d5`).
- Alla mossa 10 il Bianco gioca **cxd6**, e la notazione dice che il pedone bianco cattura
  su d6.
- Ma il pedone bianco stava su **c5**, non su c6: una cattura "normale" di un pedone da c5
  andrebbe su b6 o d6 solo se ci fosse un pezzo nemico *su quella casella*. Su d6 non c'era
  nulla — l'ultima mossa nera aveva messo il pedone su **d5**, non d6.
- Quindi, dal suo punto di vista: il pedone bianco cattura un pezzo (nero) che non si trova
  sulla casella di arrivo dichiarata, e lo fa muovendosi verso una casella vuota. Un
  comportamento che, senza conoscere la regola specifica, sembra a tutti gli effetti un bug
  di validazione mosse — l'app avrebbe dovuto rifiutare `cxd6` con "mossa illegale" e non
  lo ha fatto.

Questa lettura è ragionevole per chi non ha familiarità con questa singola regola: è
l'unica cattura degli scacchi in cui la casella di arrivo della mossa e la casella da cui
sparisce il pezzo catturato **non coincidono**.

---

## 2. La regola en passant

**En passant** ("di passaggio", FIDE Laws of Chess, art. 3.7.e) è un'eccezione speciale
alla regola di cattura dei pedoni, pensata per compensare la possibilità (introdotta più
tardi nella storia del gioco) che un pedone avanzi di due caselle dalla sua casella
iniziale invece di una sola.

Condizioni per l'en passant, tutte necessarie:

1. Un pedone avversario avanza di **due caselle** in un solo movimento (dalla sua traversa
   di partenza: 7ª per il Nero, 2ª per il Bianco), e in questo movimento passa esattamente
   accanto (stessa traversa, colonna adiacente) a un pedone proprio già posizionato lì.
2. La cattura en passant deve essere eseguita **immediatamente**, nella mossa
   dell'avversario **subito successiva** a quell'avanzata di due caselle. Se si gioca
   un'altra mossa nel frattempo, il diritto alla cattura si perde per sempre su quel
   pedone.
3. Il pedone catturante si muove sulla casella che il pedone avversario **avrebbe
   occupato se si fosse mosso di una sola casella** — non sulla casella dove il pedone
   avversario si trova effettivamente. Il pedone avversario viene rimosso dalla scacchiera
   dalla sua casella reale.

Applicato al caso segnalato:

- `9...d5` è la prima mossa di quel pedone nero (da d7), di due caselle, e lo porta esattamente
  in fiancata al pedone bianco fermo su c5. Condizione 1 soddisfatta.
- Il Bianco gioca `10. cxd6` **nella mossa immediatamente successiva**. Condizione 2
  soddisfatta.
- **d6** è precisamente la casella che il pedone nero avrebbe occupato se si fosse mosso
  di una sola casella (d7-d6) invece di due. Il pedone bianco si sposta lì; il pedone nero
  reale, fermo su **d5**, viene rimosso dalla scacchiera. Condizione 3 soddisfatta — ed è
  esattamente il punto che confondeva la segnalazione: la casella di arrivo (d6) e la
  casella del pezzo catturato (d5) sono diverse *per definizione* della regola, non per
  errore.

La notazione SAN non distingue una cattura en passant da una cattura normale (`cxd6` è
identica nella forma), il che rende la mossa ancora più facile da scambiare per un errore
se non si conosce la regola: leggendo solo il PGN non c'è alcun indizio testuale che si
tratti di en passant.

---

## 3. Verifica indipendente

Rieseguita da zero (non copiata dal precedente investigation report), con uno script
Python usando `python-chess` come ground truth indipendente dal codice applicativo:
carica lo stesso identico file PGN fornito dall'utente, rigioca tutta la mainline mossa
per mossa controllando ad ogni ply `move in board.legal_moves` **prima** del push (lo
stesso controllo, byte per byte, usato dal backend — vedi sezione 4), e ispeziona
`piece_at()` su c5/d5/d6 prima e dopo la mossa 10 per dimostrare da quale casella sparisce
davvero il pedone nero.

Output reale (rieseguito in questa sessione, `python-chess` 1.11.2):

```
Totale mosse (ply) nella mainline: 75
Header PGN: {'Event': 'Chess Lab', ..., 'Result': '1-0'}

ply=17 (9. Bianco) uci=e1e2 legal=True san=Ke2
  fen_before=r1b1k2r/pp1pppbp/5np1/2P5/NnP5/4PN2/PP3PPP/R1B1KB1R w KQkq - 1 9
ply=18 (9. Nero) uci=d7d5 legal=True san=d5
  fen_before=r1b1k2r/pp1pppbp/5np1/2P5/NnP5/4PN2/PP2KPPP/R1B2B1R b kq - 2 9
ply=19 (10. Bianco) uci=c5d6 legal=True san=cxd6
  fen_before=r1b1k2r/pp2ppbp/5np1/2Pp4/NnP5/4PN2/PP2KPPP/R1B2B1R w kq d6 0 10
  ep_square_before=43 (d6)
  has_legal_en_passant=True
  piece_at(c5) prima  = P
  piece_at(d5) prima  = p
  piece_at(d6) prima  = None
  piece_at(c5) dopo   = None
  piece_at(d5) dopo   = None  <-- deve essere None (pedone nero rimosso da qui, non da d6)
  piece_at(d6) dopo   = P  <-- pedone bianco arrivato qui

ply=20 (10. Nero) uci=b4c2 legal=True san=Nc2
Tutte le 75 mosse della mainline sono risultate legali: True
Risultato finale board: 1-0  (header PGN: 1-0)

Asserzioni isolate su 10. cxd6 (c5d6): TUTTE VERE
  fen prima di 10. cxd6 = r1b1k2r/pp2ppbp/5np1/2Pp4/NnP5/4PN2/PP2KPPP/R1B2B1R w kq d6 0 10
  c5d6 in board.legal_moves = True
  board.san(c5d6) = 'cxd6'
  board.ep_square = 43 (d6)
```

Cosa dimostra ciascun controllo:

| Controllo | Risultato | Cosa prova |
|---|---|---|
| `move in board.legal_moves` per **tutte** le 75 mosse (ply) della partita | sempre `True` | nessuna mossa della partita — non solo `cxd6` — è mai stata accettata al di fuori delle regole; la partita intera è coerente dall'inizio alla fine |
| FEN prima di `10. cxd6`: `...2Pp4/...  w kq **d6** 0 10` | 4° campo FEN = `d6` | lo stato "diritto di en passant su d6" non è un'invenzione a posteriori: è codificato nel FEN standard subito dopo `9...d5`, esattamente come previsto dalla specifica FEN |
| `board.ep_square == chess.D6` | `43` = D6 | conferma programmatica dello stesso fatto, letta dall'oggetto board invece che dalla stringa FEN |
| `board.has_legal_en_passant()` | `True` | l'en passant non è solo "disponibile sulla carta" ma è effettivamente una mossa legale in questa posizione precisa (nessun pin, nessuna interposizione di scacco che la invaliderebbe) |
| `board.san(move) == "cxd6"` | coincide esattamente con la mossa nel PGN | la SAN generata da zero da `python-chess` per `c5d6` è **identica** a quella effettivamente giocata — non è una mossa diversa che "sembra" uguale |
| `piece_at(D5)` prima = `p` (pedone nero), dopo = `None` | il pedone sparisce da **d5** | la cattura rimuove fisicamente il pezzo dalla sua casella reale (d5), non dalla casella di arrivo del pedone catturante (d6) — è la meccanica esatta della regola, verificata a livello di board, non solo di notazione |
| `piece_at(D6)` prima = `None`, dopo = `P` (pedone bianco) | il pedone bianco arriva su una casella che era vuota | conferma perché la mossa "sembrava" illegale a un lettore casual: la casella di arrivo era davvero vuota — solo che la regola en passant lo permette esplicitamente |

Il codice usato per generare questo output è stato scritto ed eseguito in questa sessione
(non recuperato da un'esecuzione precedente), a conferma indipendente che i numeri
riportati in `docs/bugs.md` Bug #7 sono riproducibili e non trascritti a mano.

---

## 4. Percorso nel codice

L'intera partita passa dalla mossa HTTP al bordo della scacchiera attraverso un unico
endpoint, `POST /game/move` (`chess_app/backend/main.py`, handler `make_move`, righe
509-589). Il tratto rilevante per la legalità della mossa è:

```python
# chess_app/backend/main.py, righe 509-529
@app.post("/game/move")
def make_move(req: MoveRequest):
    game = _get_game(req.game_id)
    board = game["board"]

    if board.is_game_over():
        raise HTTPException(status_code=400, detail="Game is already over")

    # Verifica turno del player
    player_turn = chess.WHITE if game["player_color"] == "white" else chess.BLACK
    if board.turn != player_turn:
        raise HTTPException(status_code=400, detail="Not your turn")

    # Parse e validazione mossa (supporta promozione es. e7e8q)
    try:
        move = chess.Move.from_uci(req.move_uci)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UCI format")

    if move not in board.legal_moves:
        raise HTTPException(status_code=400, detail="Illegal move")
```

Il flusso completo di una richiesta:

1. Il frontend invia `{game_id, move_uci}` (es. `{"move_uci": "c5d6"}`) — l'UCI è
   costruito interamente lato client dalla selezione casella-sorgente/casella-destinazione
   sulla board.
2. `_get_game(req.game_id)` recupera l'oggetto `game` vivo (cache in-memory, con
   fallback a ricostruzione da DB se non in cache — Fase 3) e con esso l'oggetto
   `board: chess.Board`, che rispecchia fedelmente lo stato reale della partita ply per
   ply (nessuno stato parallelo o duplicato da cui potrebbe divergere).
3. `chess.Move.from_uci(req.move_uci)` fa solo *parsing di formato* (4-5 caratteri validi:
   casella-casella[-promozione]) — non giudica affatto la legalità della mossa nella
   posizione corrente. Un UCI sintatticamente valido ma impossibile in quella posizione
   arriva comunque al controllo successivo.
4. **`if move not in board.legal_moves: raise HTTPException(400, "Illegal move")`** — è
   qui, e solo qui, che si decide se la mossa è legale. `board.legal_moves` è generato
   internamente da `python-chess` a partire dallo stato completo della board (inclusi i
   campi che nel FEN si vedono come "en passant target", diritti di arrocco, ecc.) e
   implementa **nativamente** tutte le regole degli scacchi, en passant compreso — non
   c'è nessuna lista di eccezioni o casi speciali scritta a mano nel backend applicativo.
5. Se la mossa supera il controllo, viene effettivamente giocata (`board.push(move)`,
   riga 545) e la partita procede normalmente (mossa Stockfish di risposta, persistenza,
   ecc. — non rilevante per questa analisi).

**Perché questo percorso gestisce l'en passant correttamente senza mai nominarlo**: il
backend non contiene — né ha mai contenuto — un ramo `if is_en_passant(move): ...`. Tutta
la legalità (comprese le regole "esotiche": en passant, arrocco con i suoi vincoli, promozione,
mosse che lascerebbero il proprio re sotto scacco) è delegata interamente a
`board.legal_moves` di `python-chess`, una libreria matura e ampiamente testata che
implementa le regole FIDE complete. Non essendoci alcuna logica di validazione
custom da mantenere per questo caso, non c'è stato nessun punto in cui un'eventuale gap
di implementazione potesse introdursi: `cxd6` è stata accettata perché è
**oggettivamente legale**, con lo stesso identico controllo — non uno "più permissivo" —
che avrebbe rifiutato una mossa realmente illegale (vedi il test `test_illegal_move` più sotto).

Questo stesso controllo (`move not in board.legal_moves` → HTTP 400) è duplicato in un
solo altro punto dell'applicazione, l'import PGN (`POST /games/import`, righe 900-902),
con la stessa semantica esatta — nessuna seconda implementazione della legalità mosse
esiste nel codebase.

---

## 5. Conclusione: non è un bug

**Nessun codice è stato modificato.** Questo è l'approfondimento tecnico del Bug #7 già
chiuso in [`docs/bugs.md`](bugs.md), qui verificato in modo indipendente da zero anziché
limitarsi a citare la conclusione precedente. `10. cxd6` è una cattura en passant
pienamente regolare secondo le Regole del Gioco FIDE; il backend l'ha accettata perché è
legale, non per un difetto di validazione. Il divario era nel modello mentale
dell'utente riguardo a questa specifica regola — probabilmente la meno intuitiva delle
regole di base degli scacchi — non nel software.

Punti di chiusura:

- La segnalazione originale, il PGN esatto, e la prima verifica sono documentati in
  `docs/bugs.md`, Bug #7. Questo documento non la sostituisce, la approfondisce.
- Nessuna modifica a `chess_app/backend/main.py` (validazione mosse) né a
  `chess_app/frontend/index.html` (`generateMoveCandidates`, riga 1132 — già gestisce
  l'en passant lato euristica visiva fin dal fix del Bug #4, parsing del campo EP dal
  FEN).
- **Copertura test**: `chess_app/tests/test_api.py`, classe `TestMakeMove`, contiene
  `test_legal_move` e `test_illegal_move` (righe 78-95) che esercitano lo stesso
  controllo `board.legal_moves` usato per `cxd6` — dimostrano che il gate legalità
  funziona nei due sensi generici (accetta il legale, rifiuta l'illegale). **Non esiste
  però, ad oggi, un test che esercita uno scenario di en passant end-to-end via
  `POST /game/move`** (nessuna classe/funzione con "passant" o "ep_square" nel file) — a
  differenza della copertura euristica frontend del Bug #4, la legalità backend
  dell'en passant è oggi verificata solo indirettamente (delega totale a `python-chess`,
  qui riverificata manualmente) e non da un test di regressione dedicato. Se si vuole
  chiudere anche questo gap, un test minimo aggiuntivo in `TestMakeMove` che riproduce
  la sequenza `d2d4 ... c-pawn su c5, avversario avanza di due caselle in fiancata, POST
  /game/move con la cattura ep` fisserebbe il comportamento come regressione — non
  necessario per chiudere questa segnalazione (il comportamento è già corretto e
  verificato), ma utile come rete di sicurezza futura.
