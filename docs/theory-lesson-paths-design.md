# Chess Lab — Percorsi di apprendimento per lezioni di teoria (analisi di design)

Data analisi: 19 luglio 2026

Estende [`docs/theory-lessons-design.md`](theory-lessons-design.md) (Fase 4 — Allenamento, feature
"Lezioni di teoria", **in corso di implementazione in parallelo** da altri agenti su
`feature/theory-lessons-backend` e `content/theory-lessons-opus-batch` — questo documento non tocca
`lessons.json`/`main.py`/`frontend/index.html`, è puro lavoro di valutazione). Documento di valutazione
(design/opzioni/tradeoff), non un piano di implementazione già deciso. Nessun codice scritto qui. Segue
lo stesso schema di [`docs/theory-lessons-design.md`](theory-lessons-design.md) e
[`docs/threatened-pieces-design.md`](threatened-pieces-design.md).

---

## Pitch

> "La parte didattica dovrebbe potermi dare la possibilità di accedere a lezioni essenziali per un
> percorso 'from ELO 600 to 1000', o analogo."

Richiesta esplicita dell'utente: questa è un **esempio illustrativo, non una spec ferma** — l'obiettivo
reale è "seguire lezioni per far salire il mio ELO", e l'utente è comodo a lasciare a questo documento la
scelta di una struttura `elo_min`/`elo_max` sensata. Ha anche avvisato che potrebbe avere idee più
concrete dopo aver effettivamente usato le lezioni di base — quindi questo documento **non deve
gold-plating**: propone la forma più modesta che regge l'idea, coerente con la disciplina già stabilita
dal progetto ("piccolo contenuto statico curato, valuta prima di costruire", vista sia in
`docs/theory-lessons-design.md` che in `docs/threatened-pieces-design.md`).

### Perché è plausibile e dove si incastra

Le lezioni di teoria (documento parente) risolvono il "guarda come si fa" per un singolo concetto
(un'apertura, un tema tattico, una tecnica di finale), ma **non rispondono a "cosa studio adesso, nel mio
ordine giusto?"**. Un utente alle prime armi con 5-6 lezioni disponibili non sa se iniziare dall'Italiana
o dalla forchetta di cavallo. Un **percorso** è semplicemente un ordine curato di lezioni con un obiettivo
di crescita dichiarato ("da 600 a 1000") — zero nuova infrastruttura di gioco/validazione, è puro
**sequenziamento e presentazione** di contenuto che esisterà già.

Il progetto ha già un segnale di livello: `GET /stats/progress` (`docs/growth-analytics.md`) calcola un
**ELO simulato** (`current_elo`, update Elo classico K=32, seed 1200) da tutto lo storico partite. Questo
documento lo riusa come input per suggerire "sei probabilmente pronto per il percorso X" — non introduce
nessun secondo sistema di rating.

---

## 1. Modello dati — dove vive l'informazione di percorso

Due opzioni concrete, come richiesto:

| # | Opzione | Pro | Contro | Verdetto |
|---|---------|-----|--------|----------|
| A | File separato `backend/data/lesson_paths.json` — lista ordinata di `lesson_id` per percorso, con `elo_min`/`elo_max`/titolo/descrizione **a livello di percorso** | Zero tocchi a `lessons.json`; un percorso è un **ordine curato a mano** (editoriale, non derivabile meccanicamente); una lezione può comparire in più percorsi o in un ordine non "ovvio" | Un file in più da tenere sincronizzato (id lezione inesistente = bug silenzioso se non validato) | **✅ SCELTA** |
| B | Campi `elo_min`/`elo_max` **per-lezione** direttamente su ogni oggetto in `lessons.json`, coi percorsi derivati raggruppando/ordinando le lezioni il cui range si sovrappone a un target | Nessun file nuovo | Richiede **riaprire e modificare ogni riga di `lessons.json`** — file su cui **3 agenti stanno scrivendo contenuto in questo momento** (`feature/theory-lessons-backend`, `content/theory-lessons-opus-batch`); assume che un percorso sia meccanicamente "lezioni il cui range ELO si sovrappone", che non regge (vedi sotto) | Scartata |

### Perché A, non solo per evitare conflitti di merge

Il motivo di merge/coordinamento (non toccare un file che altri agenti stanno popolando in questo
momento) è reale ma non è l'unico. Anche a bocce ferme, l'opzione B avrebbe un problema strutturale:
**un percorso non è un intervallo ELO applicato meccanicamente a un pool di lezioni**. Curare "quali
lezioni, in che ordine" per un obiettivo di crescita è la stessa decisione editoriale già presa per il
contenuto delle lezioni stesse (`docs/theory-lessons-design.md` §3: "il contenuto è dati, curato a mano,
non derivato"). Esempi concreti in cui la derivazione B si rompe:
- Una lezione di finale "tecnica" (es. opposizione re+pedone) potrebbe avere senso pedagogico **prima**
  di una tattica più avanzata anche se il suo `elo_min` nominale è più alto — l'ordine di un percorso è
  una scelta didattica, non un sort per range.
- La stessa lezione (es. `forchetta-cavallo`, motivo tattico universale) potrebbe voler comparire in **più
  percorsi** con obiettivi diversi — un range ELO singolo per-lezione non lo permette senza ambiguità
  su "a quale percorso appartiene".

Con l'opzione A, `lessons.json` resta **esattamente come lo stanno scrivendo gli altri agenti ora** — nessuna
colonna nuova, nessun retrofit. `lesson_paths.json` referenzia gli id per stringa (`lesson_ids: [...]`),
senza FK reale (è JSON statico) ma con la stessa disciplina di validazione già vista nel progetto per
contenuto curato a mano: un test/check a startup che ogni `lesson_id` in `lesson_paths.json` esiste
davvero in `lessons.json` (stesso spirito della validazione 822/822 di `eco.json` contro `python-chess`
in Fase 5) — così un id lezione sbagliato/non ancora autorato esplode a build/test time, non in
produzione.

### Forma proposta

```json
{
  "paths": [
    {
      "id": "elo-600-1000",
      "title": "Da 600 a 1000 ELO — le basi che contano di più",
      "description": "Il percorso essenziale per chi sta iniziando: un piano di apertura semplice, il motivo tattico più redditizio, e una tecnica di finale che chiunque deve conoscere.",
      "elo_min": 600,
      "elo_max": 1000,
      "lesson_ids": ["italiana-idee", "forchetta-cavallo"]
    }
  ]
}
```

Nessun cambio di schema DB (è JSON statico bundled, come `lessons.json`, `ENDGAME_DRILLS`,
`lichess_puzzles.json` — stessa filosofia dell'intero progetto: contenuto curato, zero migration).

---

## 2. Auto-raccomandazione da ELO simulato

Segnale: `GET /stats/progress` → `current_elo` (e `games_counted`, per il fallback sotto). Nessuna nuova
chiamata backend per la raccomandazione in sé — è **logica pura di frontend** che combina due risposte già
lette altrove nell'app (`current_elo` da Crescita, la lista percorsi da §1/§4).

### Algoritmo

```
funzione raccomandaPercorso(paths, progress):
    se progress.games_counted == 0:
        # nessuno storico misurabile — vedi fallback sotto
        ritorna paths ordinati per elo_min crescente, il primo

    elo = progress.current_elo
    match = paths.trova(p → elo >= p.elo_min E elo <= p.elo_max)
    se match esiste: ritorna match

    # elo fuori da ogni banda coperta
    se elo < min(paths.elo_min): ritorna il percorso con elo_min più basso
    se elo > max(paths.elo_max): ritorna il percorso con elo_max più alto
       (messaggio: "hai superato i percorsi disponibili")
    altrimenti: ritorna il percorso con la banda più vicina a elo (buco tra bande non contigue)
```

### Fallback esplicito — utente senza storico partite

Questo è il punto che il documento parente non doveva affrontare (le lezioni singole non hanno bisogno di
un livello di ingresso) e che **qui va deciso esplicitamente**, non lasciato scoperto.

Il tranello: `GET /stats/progress` con `series` vuota ritorna comunque `current_elo = seed_elo = 1200`
(`docs/growth-analytics.md`, tabella edge case). Se la raccomandazione usasse `current_elo` alla lettera
senza controllare `games_counted`, un utente **mai entrato in una partita** finirebbe con `elo=1200`
raccomandato al primo percorso il cui range lo contiene — che con bande tipo 600-1000/1000-1400 salterebbe
*proprio* il percorso base pensato per chi parte da zero, mostrando invece un percorso di livello
"intermedio". Il seed 1200 è un artefatto della formula Elo (punto di partenza della curva), non
un'affermazione "sei un giocatore da 1200".

**Decisione:** il fallback controlla `games_counted` (già presente nella risposta di `/stats/progress`,
nessun campo nuovo servito), non il valore numerico di `current_elo`. Se `games_counted == 0`:
- si ignora `current_elo` del tutto;
- si raccomanda il percorso con `elo_min` più basso disponibile;
- il frontend mostra un messaggio esplicito ("Non abbiamo ancora dati sulle tue partite — inizia da qui,
  il consiglio si affinerà dopo qualche partita") invece di presentare il match come un giudizio di
  livello.

Questa è l'unica logica nuova richiesta per l'auto-raccomandazione: nessun endpoint nuovo, nessuno stato
persistito, un `if` sul campo già esposto.

---

## 3. Esempio: "Da 600 a 1000 ELO" (illustrativo, non definitivo)

**Attenzione:** il roster completo delle 5-6 lezioni non è ancora finalizzato (autoring in corso in
parallelo su `content/theory-lessons-opus-batch`). Questo esempio dimostra **la forma**, non è una
curricula finale — due lezioni sono id reali e confermati da
[`docs/theory-lessons-design.md`](theory-lessons-design.md) §5, il resto è un placeholder di
posizionamento.

```json
{
  "id": "elo-600-1000",
  "title": "Da 600 a 1000 ELO — le basi che contano di più",
  "description": "Un piano di apertura semplice da capire e ricordare, il motivo tattico più redditizio per chi inizia, e (quando disponibile) una tecnica di finale elementare.",
  "elo_min": 600,
  "elo_max": 1000,
  "lesson_ids": [
    "italiana-idee",
    "forchetta-cavallo",
    "<slot placeholder — es. una lezione di finale 'tecnica' tipo opposizione re+pedone, non ancora autorata>"
  ]
}
```

Ordine ragionato (illustrativo): prima un'idea di apertura concreta e memorizzabile (`italiana-idee` —
occupare il centro, sviluppare, puntare f7), poi il motivo tattico a più alto rendimento per un
principiante (`forchetta-cavallo` — vedere due pezzi attaccati insieme), poi — quando esisterà — una
tecnica di finale elementare, a chiudere il percorso con "sai anche concludere una posizione vinta". Il
terzo slot resta esplicitamente vuoto/placeholder finché il roster lezioni non è completo: nessun id
inventato, nessun contenuto anticipato che questo documento non può verificare.

---

## 4. Nuovo endpoint o raggruppamento frontend puro?

| Opzione | Valutazione |
|---------|-------------|
| **Frontend fetcha `lesson_paths.json` come asset statico grezzo** | Rompe il pattern esistente: **ogni** dato in questa app arriva al frontend via `fetch(API + '/...')` su un endpoint FastAPI (`GET /training/endgames`, `GET /puzzles/themes`, ecc.) — mai un file statico servito direttamente al browser. Introdurlo richiederebbe una nuova route di file statici + CORS dedicato, per risparmiare... un endpoint da 15 righe. Non ne vale la pena. |
| **`GET /training/lesson-paths`** (nuovo, thin) | Carica il JSON una volta all'avvio (stesso pattern di `lessons.json`/`ENDGAME_DRILLS`) e lo serve. **Nessuna logica python-chess**: a differenza di `GET /training/lessons/{id}` (che deve espandere i FEN), un percorso è solo id + metadati — non c'è nulla da calcolare. Analogo diretto, per complessità, a `GET /training/endgames` (lista statica, stesso schema di sforzo). |

### Raccomandazione: sì, endpoint sottile — analogo a `GET /training/endgames`

```python
GET /training/lesson-paths
Response: {
  "paths": [
    {
      "id": "elo-600-1000",
      "title": "Da 600 a 1000 ELO — le basi che contano di più",
      "description": "...",
      "elo_min": 600,
      "elo_max": 1000,
      "lesson_ids": ["italiana-idee", "forchetta-cavallo"]
    }
  ]
}
```

Non arricchito con titoli/riassunti delle singole lezioni lato server (nessun "join"): il frontend, per
mostrare una vista Percorsi, avrà **già** `GET /training/lessons` caricato per la sotto-sezione Lezioni
(stessa tab Allenamento) — incrociare `lesson_ids` con quella lista già in memoria è puro JS, zero
richieste aggiuntive e zero duplicazione di dati tra i due endpoint. Coerente col principio "il backend
non fa lavoro che il frontend può fare gratis su dati che ha già".

**Verdetto**: earns its keep, ma per un motivo di **coerenza di pattern** (tutto passa da `/training/*`),
non di logica — è tanto sottile quanto la sua alternativa scartata, solo nel posto giusto dell'architettura.

---

## 5. Nessuna persistenza in v1

Stessa linea di design della feature Lezioni di base (`docs/theory-lessons-design.md` §4, "Perché NIENTE
persistenza dei progressi") — qui il caso è anche più debole:
- Non c'è account utente, come sempre.
- Un percorso è **1-3 lezioni**: non serve un tracker per un ambito così piccolo.
- Anche "quale lezione del percorso ho appena fatto" è ricostruibile a vista d'occhio dall'utente (sono 2-3
  item, non una playlist di 50).

**Se mai si volesse un minimo stato "dove sono nel percorso"**, la scelta è la stessa già discussa in
`docs/theory-lessons-design.md` §4 e va estesa qui in modo esplicito: un oggetto **in-memory di sessione**
(`let pathProgress = {}`, stile lo `state`/`training`/`ext` già esistenti in `frontend/index.html`), **mai
`localStorage`**. CLAUDE.md vieta `localStorage`/`sessionStorage` esplicitamente per lo **stato partita**;
questo non è stato partita, ma il progetto ha già trattato un caso analogo (flag "lezione già vista") nello
stesso modo — trattarlo diversamente qui, per un dato ancora più cosmetico (progresso in un percorso da 2-3
item), non avrebbe giustificazione. La convenzione "tutto lo stato client-side vive in un oggetto JS di
sessione, mai persistito nel browser" resta valida a prescindere dal tipo di stato, non solo per la
partita — è più semplice adottarla ovunque che spiegare perché fa eccezione qui.

**v1: nessun tracking, nemmeno in-memory.** Rimandato, come il resto.

---

## 6. Scope v1

Modesto quanto (se non più di) la feature Lezioni di base che estende:

**Dentro v1:**
- `backend/data/lesson_paths.json` con **1-2 percorsi** curati a mano (non di più — con 5-6 lezioni totali,
  più di 2 percorsi sarebbe ridondante).
- `GET /training/lesson-paths` (thin, nessun python-chess, analogo a `GET /training/endgames`).
- Validazione che ogni `lesson_id` referenziato esista davvero in `lessons.json` (test, non runtime check).
- Frontend: piccola sotto-vista "Percorsi" dentro la sezione Lezioni (stessa tab Allenamento) — lista
  percorsi con badge banda ELO, percorso raccomandato evidenziato (§2), click su una lezione del percorso
  → apre quella lezione (stesso visualizzatore già costruito per le lezioni singole, nessun nuovo
  componente UI).

**Rimandato (post-v1):**
- Tracking di progresso nel percorso (anche in-memory) — §5.
- Percorsi generati/derivati automaticamente da tag per-lezione (opzione B, scartata) — se mai servisse
  granularità più fine, è un'estensione compatibile, non necessaria ora.
- Più di 2 percorsi, percorsi non-lineari (rami/scelte) — over-engineering per 5-6 lezioni totali.

---

## 7. Effort stimato

| Attività | Ore stimate | Modello suggerito |
|----------|-------------|--------------------|
| Formato `lesson_paths.json` (1-2 percorsi) + `GET /training/lesson-paths` (thin, nessuna espansione FEN) | ~1 ora | Sonnet |
| Logica di raccomandazione frontend (match banda ELO + fallback `games_counted==0`) | ~1 ora | Sonnet |
| Curare i percorsi stessi (quali lezioni, quale ordine, quale banda ELO) — **dipende dal roster lezioni completo**, non anticipabile ora | ~1 ora (una volta che le 5-6 lezioni esistono) | Opus |
| Frontend: sotto-vista "Percorsi" (lista, badge, evidenziazione raccomandato, link a lezione) | ~1.5 ore | Fable |

**Totale: ~4-4.5 ore.** Sensibilmente meno della feature Lezioni di base (~5-6 ore) — coerente col fatto
che è puro sequenziamento/presentazione sopra contenuto che esisterà già, non nuovo contenuto o nuova
meccanica di board.

**Collocazione:** stessa area della feature Lezioni di base (Fase 4 — Allenamento), da avviare **dopo**
che il roster delle 5-6 lezioni è stabile (l'attività di curatela dei percorsi ne dipende direttamente).

---

## 8. Domande aperte

1. **Quanti percorsi in v1?** Solo "600→1000" come nell'esempio, o serve anche una banda successiva
   (es. "1000→1400") per chi supera il primo? Dipende da quante lezioni esisteranno davvero — con 5-6
   lezioni totali, 2 percorsi che condividono qualche lezione è già ragionevole, di più non lo è.
2. **Una lezione può comparire in più percorsi?** Assunto qui: sì, nessun vincolo di esclusività (`forchetta-cavallo` potrebbe comparire in più bande). Da confermare — se si preferisce ogni lezione in un solo percorso, cambia poco nel formato ma va deciso.
3. **Cosa mostrare a chi supera l'`elo_max` del percorso più alto disponibile?** Proposta minima: il
   percorso più alto resta raccomandato con un messaggio "hai superato i percorsi disponibili" invece di
   nessuna raccomandazione. Da confermare che sia sufficiente per v1.
4. **Naming degli id percorso** — `elo-600-1000` (descrittivo del range) o un id semantico tipo
   `basi-principiante`? L'esempio usa il primo per trasparenza, ma è una scelta estetica minore.
5. **Chi cura i percorsi?** Stessa raccomandazione della feature Lezioni di base: un agente Opus per la
   selezione/ordine, con revisione umana — ma solo **dopo** che il roster lezioni è chiuso, non prima.
6. **Il link "lezione → percorso di appartenenza"** (dalla vista di una singola lezione, mostrare "fa
   parte del percorso X") è un'aggiunta a costo quasi zero una volta che `GET /training/lesson-paths`
   esiste — v1 sì o rimandabile? Non richiesto esplicitamente dal pitch originale, minore.
