# Chess Lab — Valutazione dello stato del progetto

Data review: 13 luglio 2026 (Fable, su richiesta dell'utente — vedi `status.md`, "Prossimi passi" punto 3).
Perimetro: revisione completa post-iniziativa a 5 fasi (persistenza + storico + analytics + allenamento, PR #7/#8/#9/#10/#12). Solo valutazione, nessuna modifica al codice applicativo.

**Metodo** (non solo lettura): letti per intero `backend/main.py` (1645 righe), `backend/db.py`, `frontend/index.html` (2938 righe), `tests/test_api.py` (93 test), tutti i `docs/*.md` e il CLAUDE.md aggiornato; suite eseguita (93/93 verdi in ~25 s); backend scratch avviato su porta/DB isolati (`CHESS_LAB_DB`, porta 8977) e **esercitato dal vivo**: partite reali giocate e analizzate via API, import PGN (anche con header `FEN`), pipeline puzzle→SRS→debolezze, drill di finali; frontend guidato con harness jsdom sul vero `index.html` contro il backend live — 29/29 flussi verdi su tutte e 4 le tab (Gioca con assisted mode, Storico con replay/import/delete, Crescita, Allenamento con puzzle e avvio drill).

---

## 1. Stato generale

Il progetto è in uno stato **molto migliore di quanto ci si aspetterebbe** da cinque passate implementative rapide di agenti diversi (Sonnet/Opus/Fable) sullo stesso codice. L'app oggi è un trainer di scacchi personale completo e coerente: si gioca contro Stockfish a forza regolabile con coach non-AI opzionale, tutto sopravvive al riavvio, lo storico è consultabile con replay, i propri errori diventano puzzle con ripasso a intervalli, e c'è un profilo debolezze e una dashboard di crescita. Il ciclo *gioca → analizza → ripassa gli errori → misura il progresso* — il cuore del prodotto — funziona end-to-end, verificato dal vivo.

Detto senza fronzoli, però:

- **C'è un bug serio non tracciato** (`/game/analyze` ignora `start_fen` — sezione 4): analizzare un drill di finali o un import con posizione custom **appende la richiesta per sempre** e perde un processo Stockfish a ogni tentativo. È il punto d'incrocio di due feature (analisi Fase 1, `start_fen` Fase 3/4) che nessuna delle due passate ha ri-verificato — il rischio classico dello sviluppo a fasi indipendenti.
- **La roadmap è rimasta indietro rispetto alla realtà.** La Fase 5 è marcata 🔲 in CLAUDE.md ma 3 delle sue 4 righe sono di fatto già consegnate (grafico eval → `feature/analysis-panel-v2`; statistiche personali + dashboard → consolidamento in Fase 3). Resta solo l'identificazione aperture ECO. Vale la pena ri-scopare la fase (ridurla a "ECO" o fonderla in Fase 6) invece di lasciarla come fase fantasma. Analogamente, la voce WebSocket di Fase 6 ("supporto multi-tab") merita un ripensamento: per un'app locale mono-utente il valore è dubbio rispetto al costo, mentre time control e puzzle-mode esterna restano solide.
- **Il file singolo frontend sta arrivando al limite.** 2938 righe funzionano ancora, ma i segni di attrito tra codice Fase 1-2 e Fase 3-5 sono già visibili (sezione 3), e Fase 6/7 sono entrambe frontend-pesanti.

## 2. Cosa funziona bene

Cose da **preservare consapevolmente**, perché sono il motivo per cui cinque fasi si sono accumulate senza collassare:

- **Write-through cache (`_get_game`, `main.py:170`).** Il pattern "cache in-memory come hot path, DB come verità ricostruibile" ha retto ogni fase successiva senza modifiche: replay, delete (con eviction esplicita anti-resurrezione), import, drill — tutti passano dallo stesso punto e tutti sopravvivono al restart. È testato davvero (`test_cache_miss_recovery`), non solo dichiarato.
- **`buildBoardEl()` (`index.html:1376`).** Il renderer di board estratto in Fase 3 è oggi usato da **tre** board (live, replay, puzzle) senza fork del codice. È l'investimento di refactoring che si è ripagato di più in tutto il progetto: il pannello Allenamento ha ottenuto una board interattiva quasi gratis.
- **Convenzioni condivise con fonte unica.** `_result_predicate`/`_player_result`/`_game_filter_conditions` (`main.py:418-485`) sono l'unico posto dove vive la semantica win/loss/draw-relativa-al-player e il default `source='play'`; `GET /games`, `/stats/*` e `/training/weaknesses` le riusano, e il frontend (`playerResultOf`, `index.html:2207`) si dichiara esplicitamente "controparte della stessa convenzione, non calcolo indipendente". Questo è il motivo per cui i numeri tornano uguali in tutte le viste.
- **`moves.fen_before` come decisione di schema.** Persistere la posizione *prima* di ogni ply (Fase 3) ha reso replay e generazione puzzle (Fase 4) delle semplici query — zero ri-simulazione. Design anticipato che ha pagato esattamente come previsto.
- **Disciplina dei commenti e dei docs.** Ogni scelta non ovvia ha il *perché* scritto accanto (il think-time "onesto" che esclude il padding cosmetico, il no-op difensivo di `_persist_analysis`, l'anti-orfani dei puzzle nel frontend). `bugs.md`/`improvements.md`/`status.md` sono una storia leggibile del progetto, non burocrazia. Per una codebase multi-agente questo è ciò che ha tenuto basso il costo di contesto di ogni passata.
- **Isolamento pragmatico dei test** via parametro `?source=` sugli endpoint aggregati: ha permesso asserzioni deterministiche su un DB condiviso di sessione senza infrastruttura di fixture complessa.

## 3. Debito tecnico e criticità

In ordine di importanza:

1. **`frontend/index.html` — due "epoche" di codice nello stesso file.** Il codice Fase 3-5 usa l'helper `fetchJson()` (`index.html:2184`) con gestione errori uniforme; il codice Fase 1-2 (`sendMove` :1731, `startGame` :1963, `requestAnalysis` :1987, `fetchHint` :1480) usa `fetch` grezzo con quattro stili di error-handling diversi. Un manutentore umano se ne accorge subito. Stessa cosa per l'interazione board: `onSquareClick` (:1662) e `onPuzzleSquare` (:2742) sono ~40 righe quasi-duplicate della stessa macchina a stati click-seleziona-muovi, divergenti solo nell'ordine dei rami (equivalente oggi, trappola domani).
2. **`sqToXY()` (:1226) è rimasta legata a `state.playerColor`** mentre la sua inversa è stata generalizzata in `sqNameFor(i, orientation)`. Oggi è innocuo (le frecce esistono solo sulla board live), ma la valutazione in corso "threatened pieces overlay" disegnerebbe overlay proprio con queste coordinate: se mai servisse sulla board puzzle, questo è il primo muro. Da parametrizzare *prima* di quell'implementazione.
3. **Incoerenze minori ma visibili:** depth analisi hardcodata a 14 nel frontend (:1990) contro default 16 del backend e di CLAUDE.md; classe CSS `.hist-msg` usata come "messaggio generico" anche in Crescita/Allenamento; selettori chart `.ec-*` duplicati riga per riga tra `.eval-chart` e `.growth-chart` (:468-473); la board puzzle non passa `isCheck` a `buildBoardEl` (nessun highlight scacco nei puzzle, puramente cosmetico).
4. **`backend/main.py` monolitico (1645 righe).** Regge ancora, ma contiene ormai sei aree distinte (partita, analisi, storico, stats/Elo-sim, training/SRS, euristiche tattiche) più 110 righe di *dati* (`ENDGAME_DRILLS`, :1504-1617). La sezione training è un candidato naturale a `backend/training.py` (FastAPI `APIRouter`), e i drill a un modulo dati. Non urgente, ma da fare prima che Fase 6/7 aggiungano altre due aree.
5. **Concorrenza `/hint` vs `/game/move`: documentata tre volte, testata zero.** Il rischio non è più teorico come sembrava: il bug in sezione 4 dimostra che python-chess reagisce a un mismatch board/engine **appendendosi**, non fallendo pulito. Una `push()` concorrente durante `engine.analyse(board, ...)` sullo stesso oggetto `board` (che `/hint` legge vivo dalla cache) può produrre lo stesso identico pattern. Merita almeno un test di caratterizzazione, o il piccolo fix di passare a `/hint` una *copia* del board (`board.copy()`), che chiude la finestra a costo zero.
6. **L'harness di verifica frontend viene ricostruito e buttato via a ogni fase.** Fase 3, Fase 4/5 e questa review hanno tutte riscritto da zero lo stesso harness jsdom (documentato ogni volta in CLAUDE.md, mai committato). Con il vincolo permanente "nessun browser nei sandbox" (vedi Bug #6, tre tentativi per un bug *visivo*), l'harness È l'unico strumento di verifica frontend disponibile agli agenti: va versionato (vedi sezione 6).
7. **Nota minore di igiene:** `EndgameStartRequest` (`main.py:116`) duplica i campi di `NewGameRequest`; la cache `games` non ha eviction (irrilevante in locale, da ricordare se mai cambiasse il deployment); `answer_puzzle` (`main.py:1412`) non valida la legalità della mossa — una mossa illegale conta come "sbagliata" e resetta la carta SRS (accettabile per design, ma non è scritto da nessuna parte che sia intenzionale).

## 4. Bug o comportamenti sospetti non ancora tracciati

Tutti **verificati dal vivo** su backend scratch, non solo dedotti dalla lettura. Nessuno è in `bugs.md`.

### Bug A — `/game/analyze` ignora `start_fen`: richiesta appesa per sempre + leak di processi (serio)

`analyze_game()` ricostruisce la partita da **due board hardcodate alla posizione standard** — `main.py:801` (`board = chess.Board()`) e `main.py:815` (`scratch_board = chess.Board()`) — mentre tutto il resto del backend (`_load_game_from_db`, `_board_to_state`, `_build_pgn`) onora `game["start_fen"]`. Per una partita da FEN custom le mosse vengono quindi rigiocate sulla posizione sbagliata: board corrotta, e Stockfish riceve posizioni che non corrispondono alle PV che restituisce.

Riproduzione verificata: `POST /training/endgames/kr_vs_k/start` → una mossa → `POST /game/analyze` ⇒ python-chess solleva `EngineError: illegal uci ...` *dentro* il protocollo UCI e **la chiamata `analyse()` non ritorna mai**: la richiesta HTTP resta appesa indefinitamente (thread del threadpool perso), il processo Stockfish resta orfano a ogni tentativo, e il bottone "Analizza" del frontend gira per sempre. Il server nel complesso resta responsivo, ma ogni retry consuma un thread e un processo in più.

Impatto: **qualsiasi drill di finali** (Fase 4) e **qualsiasi import PGN con header `FEN`** sono inanalizzabili — quindi niente accuracy, niente `analysis_results`, niente puzzle generati da quelle partite. Fix stimato: 2 righe (`_starting_board(game.get("start_fen"))` in entrambi i punti) + test di regressione. Vedi sezione 5.

### Bug B — patta per ripetizione tripla / 50 mosse mai dichiarata né reclamabile (medio)

`_check_game_over()` (`main.py:252`) fa da gate su `board.is_game_over()`, che in python-chess **non include** le patte *reclamabili* (triplice ripetizione, 50 mosse) ma solo quelle automatiche (quintuplice, 75 mosse). Verificato: posizione ripetuta 3 volte ⇒ `can_claim_threefold_repetition()==True` ma `_check_game_over` ritorna `None` e la partita continua. Le reason `"threefold_repetition"`/`"fifty_moves"` scattano in pratica solo alle soglie 5x/75 — in contrasto con quanto dichiarato chiuso nel Bug #2 di `bugs.md` e nella checklist MVP di CLAUDE.md ("gestione tutti i game-over"). Impatto concreto: nei drill con `goal: "draw"` (Philidor, trébuchet…) l'utente che *tiene la patta* correttamente non vede mai la partita finire finché non matura la regola dei 75. Decisione da prendere: auto-claim (`is_game_over(claim_draw=True)`, coerente con l'esperienza attesa contro un engine) oppure UI di claim esplicito.

### Bug C — numerazione mosse errata nell'analisi di partite che iniziano col nero (minore, oggi mascherato da Bug A)

`analyze_game()` calcola `move_number = (ply_idx // 2) + 1` (`main.py:875`) assumendo che il ply 1 sia del bianco. Per uno `start_fen` col nero al tratto (drill Philidor), il ply 1 (nero) e il ply 2 (bianco) ricevono lo stesso `move_number` con colori invertiti rispetto alla realtà; la tabella a due colonne del frontend raggrupperebbe le semimosse in righe sbagliate. Irrilevante finché Bug A blocca del tutto l'analisi di quelle partite, ma va corretto **insieme** a Bug A o il fix di A lo farà emergere subito.

### Nit — drill `rook_pawn_win` incoerente col proprio nome

`main.py:1611`: nome/descrizione parlano di "pedone di torre", ma il FEN `8/6k1/8/8/8/8/R5K1/8` non contiene alcun pedone — è un KRvK semplice, di fatto un duplicato di `kr_vs_k` con re già decentrato. Da rinominare o sostituire con un vero finale di pedone di torre (es. `K+R+pedone-a` vs `K+R`, o il classico pedone di torre + alfiere del colore sbagliato).

## 5. Miglioramento rapido (≤1 ora)

**Fixare Bug A** (`/game/analyze` + `start_fen`): sostituire le due `chess.Board()` a `main.py:801` e `main.py:815` con `_starting_board(game.get("start_fen"))` — l'helper esiste già ed è usato ovunque altrove — e aggiungere due test di regressione: analisi di un drill avviato via `/training/endgames/{id}/start` e analisi di un import con header `FEN`. Nell'ora ci sta anche il fix contestuale di Bug C (derivare il primo colore da `board.turn` della posizione iniziale, come già fa `_create_new_game` dal fix Fase 4 — stesso identico pattern).

È il candidato giusto perché ripara un'intera feature consegnata (analisi/puzzle sui drill), elimina un hang con leak di risorse, e il costo è minimo con l'infrastruttura di test già pronta.

## 6. Investimento strutturale (una settimana)

**Rendere il frontend manutenibile e verificabile prima di Fase 6/7**, in un'unica passata coerente:

1. **Spacchettare `index.html`** in `index.html` + `css/app.css` + qualche `js/*.js` caricato con classici `<script src>` in ordine (niente ES module, niente bundler: gli script classici funzionano anche via `file://`, quindi il vincolo "apribile senza server" resta rispettato). Taglio naturale: `board.js` (rendering + interazione condivisa), `play.js`, `history.js`, `growth.js`, `training.js`, `app.js` (stato/navigazione).
2. **Sanare le divergenze tra le due epoche** durante lo split, non come lavoro separato: tutte le chiamate su `fetchJson()`, un'unica macchina di interazione board parametrizzata (fonde `onSquareClick`/`onPuzzleSquare`), `sqToXY(sq, orientation)` simmetrica a `sqNameFor`.
3. **Committare l'harness jsdom come suite smoke ripetibile** (`tests/frontend/` o `tools/`): script che avvia il backend su porta/DB scratch, carica il vero HTML, guida i flussi delle 4 tab (la review ne ha appena scritto uno da 29 check che può fare da base). È la risposta strutturale sia al vincolo "nessun browser in sandbox" sia alla lezione del Bug #6: la verifica frontend oggi si butta via a ogni fase, e ricostruirla costa più che mantenerla.

Perché ora: Fase 6 (puzzle-mode esterna, time control) e Fase 7 (pannello coach) sono quasi interamente frontend, e c'è già in coda la board ridimensionabile e la valutazione threatened-pieces — tutte mani diverse che toccherebbero lo stesso file da 3000+ righe in crescita. Il costo dello split è pagato una volta; il costo di *non* farlo si paga a ogni merge da qui in avanti. Nota: richiede di aggiornare il vincolo "file singolo" in CLAUDE.md — il vincolo che conta ("zero npm, zero build, apribile da `file://`") sopravvive intatto.

## 7. Nota sulla copertura test

93/93 verdi in ~25 s — suite sana, veloce, backend-only. Giudizio onesto: **ottima dove guarda, con angoli ciechi esattamente dove sono i bug**.

**Ben coperto:** persistenza e cache-miss (incluso `start_fen` in *creazione*), filtri e paginazione di `GET /games`, la matematica di `/stats/progress` (ricalcolo punto-per-punto della formula Elo), la progressione SM-2 esatta su 4 risposte, le euristiche debolezze (con FEN costruiti ad hoc per fork/pin/re esposto — i test più intelligenti della suite), il turno iniziale dei drill, il cascade di `DELETE` verificato e non assunto.

**Scoperto o sottile:**
- **Nessun test analizza una partita con `start_fen`** — è il buco che ha lasciato passare Bug A: `start_fen` è testato in creazione/ricostruzione ma mai incrociato con `/game/analyze`.
- **Il percorso di ripasso SRS non è mai esercitato:** nessun test crea una carta scaduta e verifica che `/training/puzzles/next` la restituisca con `is_review: true` (verificato dal vivo in questa review: funziona — ma è il cuore dello scheduling e oggi è coperto zero).
- **Nessun test di promozione** end-to-end su `/game/move`, benché sia nella checklist di testing di CLAUDE.md sin dall'MVP; **nessun game-over raggiunto giocando** (checkmate/stalemate arrivano solo da board iniettate; il 400 "Game is already over" su `/game/move` non è testato).
- **Concorrenza `/hint`+`/game/move`:** rischio documentato in tre punti dei docs, zero test (vedi sezione 3, punto 5).
- **Import PGN con header `FEN`** non testato (verificato dal vivo: l'import funziona, l'analisi successiva ricade in Bug A).
- **Frontend: zero test committati** per ~2900 righe che sono oltre metà del codice del progetto — gli harness jsdom di Fase 3/4-5 hanno verificato bene, una volta, e sono andati persi (sezione 6, punto 3).

In proporzione: 93 test per ~20 endpoint è una buona *ampiezza*, e la qualità media delle asserzioni è alta. Il gap non è quantitativo ma di *incroci*: le feature nuove sono testate nel loro percorso felice e nei loro edge interni, ma non contro le feature vecchie che toccano (`start_fen`×analyze, SRS×tempo, hint×move). I prossimi test da scrivere sono quelli delle intersezioni, non altri test unitari.
