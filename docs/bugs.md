# Chess Lab — Bug noti (Fase 1)

## Bug #1 — `move_san` ritorna UCI invece di SAN nell'analisi
**File:** `backend/main.py` → `analyze_game()`
**Priorità:** Alta
**Stato:** Fixato nel backend MVP

**Problema:** Il campo `move_san` nella risposta di `/game/analyze` a volte conteneva la stringa UCI (es. `"e2e4"`) invece della notazione algebrica standard (es. `"e4"`). Questo accadeva perché la conversione SAN veniva fatta *dopo* aver pushato la mossa sulla board, quando `board.san(move)` non è più valida.

**Fix:** Calcolare `board.san(move)` **prima** di `board.push(move)`. La SAN dipende dalla posizione corrente (es. per disambiguare `Nbd2` vs `Nfd2`), quindi deve essere calcolata quando la mossa è ancora legale nella posizione attuale.

---

## Bug #2 — Game-over per stalemate non gestito correttamente
**File:** `backend/main.py` (backend) + `frontend/index.html` → `showGameOver()` (frontend)
**Priorità:** Media
**Stato:** Fixato nel backend MVP (frontend da verificare)

**Problema:** Il backend gestiva solo il checkmate come condizione di fine partita. Stalemate, regola delle 50 mosse, ripetizione tripla e materiale insufficiente non venivano rilevati. La partita continuava o il server restituiva un errore.

**Fix:** Aggiunta funzione `_check_game_over()` che verifica tutte le condizioni:
- `board.is_checkmate()` → `"checkmate"`
- `board.is_stalemate()` → `"stalemate"`
- `board.is_insufficient_material()` → `"insufficient_material"`
- `board.can_claim_fifty_moves()` → `"fifty_moves"`
- `board.can_claim_threefold_repetition()` → `"threefold_repetition"`

La risposta include un campo `game_over` con `result` (es. `"1/2-1/2"`) e `reason`.

---

## Bug #3 — Promozione pedone: pezzi del colore sbagliato nel modal
**File:** `frontend/index.html` → `askPromotion()`
**Priorità:** Media
**Stato:** Da fixare nel frontend

**Problema:** Quando il player è nero e promuove un pedone, il modal mostra i pezzi bianchi (♕♖♗♘) invece dei neri (♛♜♝♞). Il modal usa sempre i caratteri Unicode bianchi indipendentemente dal colore del player.

**Fix previsto:** La funzione `askPromotion()` deve ricevere il colore del player e selezionare i caratteri Unicode corretti:
- Bianco: `♕ ♖ ♗ ♘`
- Nero: `♛ ♜ ♝ ♞`

---

## Bug #4 — `generateMoveCandidates` non gestisce en passant
**File:** `frontend/index.html`
**Priorità:** Bassa
**Stato:** Da fixare

**Problema:** La funzione client-side che genera le mosse candidate per l'highlight visivo non rileva correttamente l'en passant in tutti i casi edge. Essendo solo un'euristica visiva (la validazione reale è sul backend), l'impatto è cosmetico: il dot verde non appare sulla casella di cattura en passant.

**Fix previsto:** Parsare il campo en passant dal FEN (4° campo) e aggiungere la casella target come mossa candidata quando un pedone è nella posizione corretta (rank 5 per bianco, rank 4 per nero).

---

## Bug #5 — Nessun test automatico
**File:** `tests/`
**Priorità:** Alta
**Stato:** Fixato — creato `test_api.py`

**Problema:** Il progetto non aveva test automatici, rendendo impossibile verificare regressioni dopo modifiche.

**Fix:** Creata suite pytest con copertura di: creazione partita (bianco/nero), mosse legali/illegali, analisi post-partita con verifica SAN, health check.

---

## Bug #6 — Pezzi sulla board percepiti con colori invertiti (bianco/nero)

**File:** `frontend/index.html` → CSS `.square`, `.promo-piece`
**Priorità:** Media
**Stato:** Fix applicato, da confermare visivamente (non è stato possibile verificare a schermo in questo ambiente, vedi nota sotto)

**Problema:** L'utente riporta che sulla scacchiera i pezzi bianchi sembrano neri e/o viceversa.

Verifica del codice (nessun bug logico trovato):
- `PIECES` (riga 676) mappa i caratteri FEN maiuscoli (bianco, per convenzione FEN) ai glifi Unicode U+2654-2659 "WHITE CHESS X" e i minuscoli (nero) a U+265A-265F "BLACK CHESS X" — mapping corretto e conforme alla convenzione Unicode standard (il nome Unicode "white/black" indica lo stile del glifo: outline/hollow per "white", solid/filled per "black").
- `renderBoard()` (righe 904-908) assegna `.white-piece` quando il carattere FEN è maiuscolo e `.black-piece` quando è minuscolo — corretto.
- Il CSS (`.white-piece` → `color:#fff` + contorno scuro; `.black-piece` → `color:#222` + contorno chiaro) è coerente con l'intento.
- Nessuna delle tre parti (mappa, classi, CSS) contiene un'inversione logica dimostrabile: la catena FEN → glifo → classe → colore è corretta su carta.

Causa più probabile: prima di `.square` non era dichiarato nessun `font-family` dedicato ai glifi scacchistici — si ereditava `'Segoe UI', system-ui, sans-serif` dal `body`. Su molti stack di sistema (in particolare Linux, incluso questo ambiente Linux/WSL2) il font di fallback effettivamente scelto dal browser per i codepoint U+2654-265F può non implementare in modo distinto/coerente lo stile "outline" (bianco) vs "solid" (nero) previsto dalla convenzione Unicode — rendendo i due set visivamente indistinguibili o percepiti come invertiti indipendentemente dal `color` CSS applicato. Non è stato trovato un riferimento pubblico che documenti un'inversione sistematica in un font specifico (vedi nota di verifica sotto): la causa è quindi trattata come "font di fallback inconsistente", non come bug di un font nominato.

**Fix:** Dichiarato un font-family esplicito su `.square` (board, riga ~139) e su `.promo-piece` (modal promozione, riga ~567): `'Noto Sans Symbols2', 'DejaVu Sans', 'Segoe UI Symbol', 'Arial Unicode MS', sans-serif` — famiglie note per implementare l'intero blocco scacchistico Unicode con distinzione outline/solid coerente con la convenzione standard. Non toccata la mappa `PIECES` né la logica `isWhitePiece` usata altrove per validità mosse (riga 762+), come da richiesta esplicita.

**Nota di verifica:** non è stato possibile confermare visivamente il bug né il fix in questo ambiente: nessun browser disponibile (nessun binario Chromium installato; il download via Playwright fallisce al lancio per librerie di sistema mancanti come `libnspr4.so`), nessun `fontconfig` installato (`ldconfig -p` non trova `libfontconfig`), e l'unico font TrueType presente sul sistema (Ubuntu) non copre affatto il blocco Unicode U+2654-265F (verificato via `fontTools`). Serve conferma visiva umana nel browser reale dell'utente.

---

**Fix v2 (definitivo) — 11 luglio 2026**
**Stato:** Applicato, da confermare visivamente (stesso limite ambientale del fix precedente: nessun browser disponibile in questo sandbox)

**Problema persistente:** dopo il fix v1 (font-family esplicito), l'utente ha confermato che i pezzi continuano a sembrare invertiti. Causa più probabile, più specifica della precedente: il set "outline" (♔♕♖♗♘♙, U+2654-2659) è un glifo con interno vuoto/trasparente. Applicandogli `color:#fff` (vedi `.square.white-piece`), solo i tratti sottili del contorno prendono il colore — l'interno del glifo resta trasparente e lascia trasparire il colore della casella sottostante. Il pezzo bianco finisce per leggersi come una sagoma sottile scura (dominata dal `-webkit-text-stroke` scuro), cioè esattamente "invertito". Questo è indipendente dal font scelto: qualunque font che rispetti la semantica Unicode outline/solid soffre dello stesso problema quando il fill CSS viene applicato a un glifo cavo.

**Fix:** eliminata la dipendenza da due *forme* di glifo diverse per i due colori. `PIECES` (frontend/index.html) ora mappa sia i caratteri FEN maiuscoli che minuscoli allo stesso set "solid" (♚♛♜♝♞♟, U+265A-265F) — un glifo pieno, che riceve correttamente il fill CSS su tutta la sagoma. La distinzione bianco/nero resta **esclusivamente** nelle classi `.white-piece`/`.black-piece` già esistenti (colore + stroke di contrasto), invariate. Corretto anche un secondo punto con lo stesso difetto non coperto dal fix v1: `askPromotion()` hardcodava i due set di glifi per colore senza alcuna colorazione CSS di sicurezza (vedi commento riga 564-566 del fix v1) — ora usa lo stesso set solid e applica `.white-piece`/`.black-piece` al modal, ereditando la colorazione della board. Il font-family esplicito del fix v1 resta (righe ~139, ~567): non è più la difesa principale ma rimane utile per la resa di U+265A-265F su sistemi con font scarsi.

**Nota di verifica:** stesso limite ambientale del fix v1 — nessun browser disponibile in questo sandbox per conferma visiva. Il ragionamento (glifo cavo + fill CSS = trasparenza interna) è verificabile a schermo aprendo `frontend/index.html` in un browser reale: i pezzi bianchi devono ora apparire come sagome piene bianche con contorno scuro, i neri come sagome piene scure con contorno chiaro, su entrambi i colori di casella.

---

**Fix v3 (risolutivo) — 11 luglio 2026**
**Stato:** Applicato

**Problema persistente:** l'utente ha confermato via browser reale che anche il fix v2 (glifo unico solid + colorazione CSS) non basta — i pezzi restano poco riconoscibili. Causa di fondo, comune a v1 e v2: qualunque rendering basato su glifi Unicode dipende dal font effettivamente disponibile sul sistema dell'utente per la resa esatta della forma — non c'è modo di controllarla al 100% via CSS, per quanto si scelga il glifo o il font-family.

**Fix:** abbandonato il rendering via glifo Unicode. I pezzi ora sono veri asset SVG — il set "Cburnett" di Lichess (`chess_app/frontend/pieces/*.svg`, 12 file, licenza GPLv2+, vedi `pieces/NOTICE.md`), scelto dall'utente per massima riconoscibilità (è il set default di Lichess). `PIECES` è stato sostituito da `PIECE_FILES` (mappa carattere FEN → nome file) e da una funzione `pieceImg(fenChar)` che crea un `<img class="piece-img" src="pieces/{file}.svg">`; usata sia in `renderBoard()` che in `askPromotion()` (che ora non hardcoda più due set di glifi per colore). Le classi CSS `.white-piece`/`.black-piece` e il `font-family` dedicato ai glifi scacchistici sono stati rimossi: il colore è nel file SVG stesso (fill/stroke), non più delegato a CSS su testo. Asset serviti come file statici accanto a `index.html` (non inline, non npm) — restano apribili via `file://` senza server, coerente col vincolo esistente.

**Nota di verifica:** stesso limite ambientale delle versioni precedenti — nessun browser disponibile in questo sandbox. A differenza dei fix precedenti, però, qui il colore non dipende più dal font di sistema: i file SVG hanno `fill` esplicito nel markup, quindi il rischio di regressione per font-fallback è eliminato strutturalmente, non solo mitigato. Verifica visiva umana comunque raccomandata alla prima apertura.
