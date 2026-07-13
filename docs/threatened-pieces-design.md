# Chess Lab — Pezzi in presa (analisi di design)

Data analisi: 11 luglio 2026

Estende la **Fase 2 — Assisted Play**. Documento di valutazione (design/pareri/migliorie),
non un piano di implementazione con stime orarie. Nessun codice scritto qui.

---

## Pitch

> "Valutiamo l'idea di poter inserire nella modalità assistita delle informazioni visive
> sulla scacchiera per evidenziare i pezzi in pericolo — sotto minaccia dell'avversario."

Richiesta esplicita dell'utente: **valutare** l'idea, raccogliere **pareri** e **migliorie** —
non limitarsi a descrivere un'implementazione. Questo documento quindi commenta criticamente,
propone varianti e ne scarta alcune con motivazione.

**Chiarimento dell'utente (recepito):** con "in pericolo" si intende il termine scacchistico
**"pezzi in presa"** — pezzo **attaccato e completamente indifeso**, che si perderebbe gratis.
Non "qualsiasi pezzo attaccato" (troppo rumoroso) e non un'analisi di cambio materiale completa.
Questa è la definizione **committed** per la v1; le alternative restano discusse sotto come
ragionamento e come rifinitura futura, ma **la scelta è fatta**: v1 rileva i pezzi in presa.

### Perché è plausibile

Oggi la modalità assistita mostra la mossa migliore (frecce), le candidate MultiPV e la eval bar:
tutto **prescrittivo** ("gioca qui"). Manca invece il livello **diagnostico** e passivo: *"attento,
questo pezzo lo stai per perdere gratis"*. Pedagogicamente vedere che un pezzo è in presa **prima**
di muovere vale molto più che scoprirlo nel pannello di analisi post-partita (`/game/analyze`), dove
ormai il blunder è già stato commesso. La stragrande maggioranza degli errori sotto i 1600 ELO è
materiale lasciato gratis; un warning sul pezzo indifeso attacca esattamente quella classe di errori,
nel momento giusto.

L'idea è inoltre **gratis** dal punto di vista computazionale (vedi Architettura): non richiede
Stockfish, si calcola dal solo FEN con `python-chess`. Questo la rende molto più economica del `/hint`
esistente e cambia le regole del gioco su *quando* può essere aggiornata (sempre, non on-demand).

---

## 1. Cosa significa "in presa" — lo spazio di design

La parola "minaccia" è ambigua e la scelta della definizione **è** il design. Quattro livelli, dal più
ingenuo al più costoso, per motivare *perché* la definizione scelta (#2) è quella giusta:

| # | Definizione | Come si calcola | Verdetto |
|---|-------------|-----------------|----------|
| 1 | **Casella attaccata** — un mio pezzo su una casella con `board.attackers(opp, sq)` non vuoto | Solo FEN, nessun engine | **Scartata: troppo rumorosa.** Ogni pezzo in una catena di pedoni risulta "attaccato" anche se la ripresa è ininfluente o favorevole. Griderebbe al lupo di continuo e il segnale verrebbe ignorato. |
| 2 | **In presa (hanging)** — attaccato **e** non difeso (`board.attackers(me, sq)` vuoto) | Solo FEN | **✅ SCELTA per la v1.** Azionabile e inequivocabile ("lo perdi gratis"), esattamente il termine "pezzo in presa". Costo trascurabile, nessun falso allarme sui pezzi ben difesi. |
| 3 | **Material-aware / SEE-like** — confronta valore attaccante vs difensore lungo la sequenza di cambi | Solo FEN (Static Exchange Evaluation) | **Rifinitura v2** (§5). Cattura anche il caso "difeso ma male" (donna difesa da torre, attaccata da pedone). Corretta ma più della richiesta attuale; nasconde nel rumore i casi meno urgenti. |
| 4 | **Eval swing (Stockfish)** — chiedere all'engine quanto cala la valutazione se l'avversario cattura | Chiamata engine (~1-2s) | **Scartata: ridondante.** È esattamente ciò che le frecce `/hint` già dicono implicitamente. Costoso e sposta la feature nel percorso lento. |

### Raccomandazione committed: livello 2 — pezzo in presa

Un pezzo del giocatore è **in presa** se, nella posizione corrente, è **attaccato da almeno un pezzo
avversario** e **non difeso da nessun proprio pezzo**:

```
in_presa(sq) :=  board.attackers(opponent, sq) non vuoto
             AND board.attackers(me, sq)       vuoto
```

Con `python-chess` sono due chiamate a `board.attackers()` per casella occupata dal giocatore. La
semantica è già quella corretta: `attackers()` tiene conto degli attaccanti reali (un pezzo inchiodato
non conta come difensore effettivo dipende dal caso — vedi §6 sui limiti noti). Il re non entra mai:
se attaccato è **scacco**, già coperto da `.king-check`.

Perché il livello 2 e non gli altri:
- **Scarto il livello 1** (attaccato-e-basta): il rumore distruggerebbe la fiducia nel segnale. Un
  highlight che si accende sempre viene ignorato sempre. Un pezzo attaccato **ma difeso** non è "in
  presa": riprendi e sei a posto.
- **Rimando il livello 3** (SEE) a rifinitura v2: è corretto e cattura più casi (il pezzo difeso male),
  ma va **oltre** la richiesta esplicita ("in presa") e rischia di diluire il segnale forte ("gratis!")
  in una scala di sfumature. Meglio partire dal caso netto e inequivocabile, poi semmai raffinare.
- **Scarto il livello 4**: duplica il lavoro di `/hint`. Il valore aggiunto di questa feature è essere
  **complementare e a costo quasi-zero** rispetto all'engine, non un secondo engine.

**Fuori portata di ogni livello (vedi §6):** minacce *combinate* — inchiodature, infilate, attacchi
di scoperta, forchette che minacciano due pezzi. Non sono "il pezzo X è in presa ora": sono tattiche a
più mosse. Rilevarle è un motore tattico, non questa feature. Il livello 2 valuta **una casella alla
volta, nella posizione corrente**. Punto.

---

## 2. Architettura — dove vive il calcolo

Tre opzioni, valutate contro i vincoli **già documentati** (`/hint` costa ~1-2s ed è quindi
on-demand; endpoint sincroni nel threadpool che possono sovrapporsi sulla board condivisa):

| Opzione | Pro | Contro |
|---------|-----|--------|
| **A. Estendere `/hint`** (nuovo campo `in_presa` nel payload) | Zero endpoint nuovi; la board è già lì | **Accoppia una cosa cheap a una cosa lenta.** I pezzi in presa si aggiornerebbero solo quando l'utente chiede un hint costoso — l'opposto di quel che serve (cambiano ad **ogni** mossa e devono essere sempre aggiornati). |
| **B. Nuovo endpoint leggero** `GET /game/{id}/threats` — puro `python-chess`, nessuno Stockfish | Microsecondi, non secondi. Sempre-attivabile, disaccoppiato dal costo dell'engine. Fonte di verità sul backend (coerente con "le mosse legali lato client sono solo euristiche"). | Un round-trip di rete (su localhost trascurabile, e nessun processo engine da spawnare). |
| **C. Interamente client-side** in JS | Nessun round-trip | Richiederebbe **reimplementare in JS** la generazione degli attaccanti con inchiodature/x-ray: fragile ed error-prone. Il progetto ha già una linea netta ("la fonte di verità è sempre il backend"; `generateMoveCandidates` è dichiaratamente solo euristica visiva). Rifarlo bene = riscrivere `python-chess` in JS. |

### Raccomandazione: opzione B — `GET /game/{id}/threats`

Un endpoint dedicato e **stateless** (funzione pura della posizione corrente, non muta nulla, non tocca
il DB). `python-chess` espone già `board.attackers(color, square)` con la semantica corretta;
reimplementarlo in JS sarebbe un downgrade di correttezza.

Perché **non** l'opzione A: il punto di forza dell'idea è che i pezzi in presa sono *position-static e
cheap* — possono e devono aggiornarsi ad **ogni** cambio di posizione, mentre `/hint` resta on-demand
perché costa. Fonderli significherebbe o pagare l'engine ad ogni mossa (bandito dai vincoli
anti-latenza) o lasciare il dato stale finché non si chiede un hint. Separarli è la scelta giusta.

Perché **non** l'opzione C: rispetta la regola architetturale esistente e riusa codice corretto invece
di riscrivere move-generation nel browser.

**Nota di concorrenza:** vale lo stesso caveat di `/hint` (può leggere un FEN che una `push()`
concorrente sta per superare), ma qui l'impatto è **ancora più basso**: nessun engine, risposta
istantanea, nessuna scrittura. La finestra di race è di microsecondi ed è read-only. WAL/threading
già in place non richiedono nulla di aggiuntivo.

**Convenienza additiva (facoltativa):** dato che `/hint` ha già la board in mano, si *può* includere
lo stesso campo `in_presa` anche nella sua risposta a costo zero — utile quando l'utente chiede
comunque un hint. Ma il percorso **autoritativo e sempre-attivo** resta il `GET /threats` dedicato.

### Shape proposto

```python
GET /game/{id}/threats
# Nessun body/param necessario: funzione pura della posizione corrente.
Response: {
  "side": "white",              # lato al tratto = i cui pezzi valutiamo (vedi §7)
  "in_presa": [
    {
      "square": "d4",
      "piece": "N",             # pezzo in presa (lettera FEN)
      "value": 3,               # valore convenzionale (P1 N/B3 R5 Q9), per ordinare/enfasi
      "attackers": ["e5", "c3"] # caselle da cui parte la minaccia (≥1)
    }
  ]
}
# 400 se la partita è già finita (come /hint).
```

Nota: in v1 ogni elemento è per definizione **indifeso** (`defended = false` implicito), quindi il
campo non serve. Comparirà se/quando la v2 SEE introdurrà i pezzi difesi-male (§5).

---

## 3. Trattamento visivo — non deve collidere con gli overlay esistenti

La board può già mostrare **quattro** linguaggi visivi. Prima di aggiungerne un quinto, l'inventario
(letto da `frontend/index.html`):

| Elemento | Trattamento attuale |
|----------|---------------------|
| `.selected` | Sfondo **verde** pieno `rgba(20,85,30,0.5)` |
| `.last-move` | Sfondo **giallo-verde** `rgba(155,199,0,0.45)` |
| `.king-check` | Sfondo **rosso** pieno `rgba(204,51,51,0.55)` |
| `.legal-move` / `.legal-capture` | Dot/anello **verde** al centro/bordo casella |
| Frecce `/hint` (`.arrow-layer`, SVG, z-index 5) | Verde / blu / giallo (`HINT_COLORS`) |
| Badge analisi | `--red` blunder, `--orange` mistake, `--yellow` inaccuracy |

Vincoli che ne derivano:
- Il **rosso pieno di sfondo** è già "scacco al re" → un pezzo in presa **non** deve usare uno sfondo
  pieno, o si confonde con lo scacco.
- Il **verde** è ovunque (selezione, mosse legali, freccia primaria) → da evitare.
- Le **frecce** occupano il centro delle caselle e lo strato SVG z-index 5.

### Raccomandazione: glow inset sul **contorno** della casella, in rosso

Non uno sfondo pieno (collide con selezione/last-move/check) e non un anello circolare (collide con
`.legal-capture`): un **box-shadow inset** che "abbraccia" il bordo interno della casella del pezzo in
presa — forma **rettangolare arrotondata**, distinta sia dal cerchio delle mosse legali sia dagli
sfondi pieni. Semanticamente il rosso = pericolo è naturale; la distinzione dallo scacco è nella
**forma** (glow di contorno vs. sfondo pieno), non nella tinta.

Bozza CSS (per fissare le idee, non da implementare qui):

```css
/* v1: singolo tier — pezzo in presa (indifeso) = rosso */
.square.in-presa { box-shadow: inset 0 0 0 3px var(--red), inset 0 0 10px 2px rgba(204,51,51,0.55); }
```

- **Un solo colore in v1** (`--red` = pericolo grave, già la semantica dei blunder nell'app): il pezzo
  in presa **è** il caso grave "gratis", non c'è una scala. L'eventuale secondo tier arancio
  (`--orange`) è riservato alla v2 SEE per i cambi sfavorevoli-ma-non-gratis (§5).
- **Sotto le frecce** nella pila visiva: le frecce (z-index 5) restano leggibili sopra il glow. Il glow
  è sulla `.square` (sotto lo strato SVG), quindi i due non competono per il pixel centrale.
- **Animazione**: al più un pulse **molto** sobrio (esiste già `@keyframes pulse`, usato dalla eval bar
  in loading). Preferibile **statico** in v1: un highlight che pulsa su più pezzi diventa caotico.
- **Coesistenza con selezione/last-move**: sono sfondi, il glow è un contorno → si sommano senza
  annullarsi (un pezzo può essere sia `last-move` sia `in-presa`). Verificare solo il contrasto sul
  quadrato scuro `--black-sq`.

---

## 4. Anti "grido al lupo" — perché la definizione #2 lo garantisce quasi da sola

Il rischio numero uno di questa feature è la **falsa urgenza**: evidenziare una donna difesa due volte
come se fosse in pericolo distrugge la credibilità del segnale. La scelta del livello 2 lo previene
**per costruzione**: un pezzo difeso — anche male — **non** viene mai evidenziato in v1. Il glow si
accende **solo** quando il pezzo è davvero prendibile gratis. Questo è il tradeoff consapevole della
v1:

- **Zero falsi positivi rumorosi** (nessun pezzo difeso segnalato) — massima fiducia nel segnale.
- **Prezzo**: alcuni falsi *negativi* — la donna difesa dalla torre e attaccata dal pedone è "difesa"
  quindi **non** evidenziata, pur essendo di fatto persa in cambio. È esattamente il buco che la
  rifinitura SEE (§5.1) chiuderà in v2. Accettabile per una v1: meglio un segnale forte e sempre
  vero che uno completo ma rumoroso.

---

## 5. Migliorie ed estensioni (le "migliorie" richieste)

Oltre alla base, le estensioni che valgono davvero la pena — con giudizio su cosa merita v1 e cosa no:

1. **Material-aware / SEE (il livello 3) — la rifinitura naturale.** *(v2 designata)* Estendere da
   "indifeso" a "prendibile in perdita di materiale" copre il caso donna-difesa-da-torre-attaccata-da-
   pedone che la v1 manca (§4). Si aggiunge un secondo tier arancio (`--orange`, cambio sfavorevole ma
   non gratis) accanto al rosso (in presa/gratis), riusando la semantica colore già in app. **Ancora
   senza engine** (SEE = funzione della sola posizione), quindi cheap. È la prima cosa da fare dopo la
   v1, ma resta fuori dalla v1 perché va oltre la richiesta esplicita ("in presa") e introduce una
   scala dove ora c'è un binario netto.

2. **Minacce prospettiche — "se giochi questa, lasci in presa quello".** *(la miglioria più preziosa,
   post-v1)* Al passaggio del mouse su una mossa legale candidata, ricalcolare i pezzi in presa sulla
   posizione **risultante** e mostrare cosa quella mossa lascerebbe indifeso. È il salto da diagnostico
   ("ora sei in presa") a preventivo ("stai per metterti in presa") — didatticamente superiore, perché
   intercetta il blunder *prima* che accada. **Ancora senza engine** (livello 2 su un push ipotetico),
   quindi cheap. Non entra in v1 solo perché è un secondo flusso UI (hover → posizione ipotetica →
   ricalcolo) e conviene stabilizzare prima quello statico.

3. **Attaccanti multipli.** Un pezzo in presa da ≥2 avversari (già visibile in `attackers`) può avere
   un piccolo badge-conteggio, o un glow leggermente più marcato. Basso costo, ma rischio clutter:
   **post-v1**, opzionale.

4. **Specchio offensivo — pezzi avversari che *tu* puoi vincere.** Evidenziare i pezzi avversari in
   presa **per me** trasformerebbe la feature da difensiva a spotter di tattiche. **Pareri contrari,
   default OFF / fuori v1**: dà via le tattiche belle e pronte, erodendo il "trova-tu-la-mossa" che è
   una linea di design deliberata (cfr. Coach Mode, che per lo stesso motivo non passa mai il best move
   a Claude). Se mai la si aggiunge, dietro un toggle separato e con consapevolezza che sposta l'app da
   "trainer" a "assist forte".

5. **Motivo testuale nel pannello hint.** Riusare il pannello assistito per una riga sintetica ("Cavallo
   in d4 in presa dal pedone e5") accanto alle candidate. Basso costo (i dati ci sono già nella
   risposta `/threats`), buon rinforzo per chi non "vede" ancora il glow. Candidabile a v1 se il glow da
   solo risulta poco esplicito.

---

## 6. Rischi e tradeoff

| Rischio | Gravità | Mitigazione |
|---------|---------|-------------|
| **Grido al lupo** (pezzo difeso segnato come in pericolo) | Alta | Definizione #2: solo pezzi **indifesi**. Un pezzo difeso non si accende mai in v1 (§4). |
| **Falsi negativi** (pezzo difeso-male, di fatto perso, non evidenziato) | Media | Tradeoff v1 accettato consapevolmente; chiuso dalla rifinitura SEE v2 (§5.1). |
| **Clutter visivo** (quinto strato su frecce + eval bar + selezione + last-move) | Media | Glow di contorno (non sfondo), rosso singolo, niente animazione aggressiva. Solo i **propri** pezzi, solo al **proprio** turno (§7). |
| **Performance** se qualcuno lo infila nel `/hint` lento | Media | Endpoint dedicato senza engine (§2). Regola: la detection **non spawna mai Stockfish**. |
| **Scope creep** verso un motore tattico completo (pin/infilata/scoperta/forchetta multi-mossa) | Alta | Confine netto: casella singola, posizione corrente. Niente combinazioni, niente look-ahead. Le tattiche restano dominio di `/hint` (engine) e Fase 7 (coach). |
| **Attaccante inchiodato** (un pezzo che "attacca" ma è inchiodato al proprio re non è una vera minaccia) | Bassa | `board.attackers()` è pseudo-attacco: possibile raro falso positivo. Accettabile in v1; la SEE v2 o un check `is_pinned` lo affinerebbero. Documentarlo. |
| **Falsa sicurezza** ("niente rosso ⇒ sono a posto") | Media | Copre solo minacce dirette di cattura su pezzo indifeso, non tattiche né cambi sfavorevoli. Chiarirlo nel tooltip del toggle, come per le euristiche "temi probabili" di Fase 4. |

---

## 7. Framing pedagogico e default

**Assist o training aid?** Sono due prodotti diversi:
- *Assist* ("dammi la verità così non lascio pezzi") → sempre-on, evidente.
- *Training aid* ("avvisami di un pericolo che potrei non vedere") → sobrio, opt-in.

**Raccomandazione:** vive **dentro** la modalità assistita, come **sotto-toggle** dedicato
("Evidenzia pezzi in presa"), **non** nel gioco normale. Motivi:
- La distinzione assistita-vs-normale è una **linea di design deliberata** del progetto. La modalità
  assistita è già il luogo dell'"aiutami"; i pezzi in presa vi appartengono. Il gioco normale deve
  restare la palestra onesta dove sbagli e impari — mettere il warning lì cancellerebbe quella linea.
- Sotto-toggle (non legato al toggle frecce/eval) perché alcuni vorranno l'eval bar ma non il glow, o
  viceversa. Default: **on** quando si entra in assistita (è il motivo per cui si entra in assistita),
  ma disattivabile indipendentemente.
- **Solo i propri pezzi, solo al proprio turno.** Evidenziare i pezzi avversari in presa è la miglioria
  §5.4 (offensiva), tenuta fuori. Mostrare i propri pezzi quando è il turno avversario aggiunge solo
  rumore: agisci nel tuo turno.

---

## 8. Scope v1

Versione minima e di valore:

**Dentro v1:**
- `GET /game/{id}/threats` — puro `python-chess`, nessuno Stockfish. Ritorna i **propri** pezzi
  (lato al tratto) **attaccati e indifesi** (definizione #2), con le caselle attaccanti.
- Frontend: sotto-toggle "Evidenzia pezzi in presa" **dentro** la modalità assistita; glow inset
  `.in-presa` (rosso) sulle caselle; refresh ad **ogni** cambio di posizione (dopo `/game/new` e ogni
  `/game/move`), disaccoppiato dal `/hint` on-demand.
- Solo pezzi propri, solo al proprio turno.
- Tooltip che chiarisce il limite ("solo pezzi indifesi attaccati — non cambi sfavorevoli né tattiche").

**Rimandato (post-v1):**
- **Material-aware / SEE** (§5.1) — v2 designata: copre i pezzi difesi-male, secondo tier arancio.
- Minacce **prospettiche** su hover delle candidate (§5.2).
- Badge attaccanti-multipli (§5.3).
- Specchio offensivo sui pezzi avversari (§5.4) — se mai, dietro toggle separato.
- Motivo testuale nel pannello (§5.5) — promuovibile a v1 se il solo glow risulta poco chiaro.

**Fuori scope (di ogni versione):** rilevazione di tattiche combinate (pin, infilata, scoperta,
forchetta multi-mossa). Quello è `/hint` (engine) e la Fase 7 (coach), non questa feature.
