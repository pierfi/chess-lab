# Note di ricerca — pipeline di sourcing per le lezioni di teoria

Data: 19 luglio 2026

**Cosa NON è questo documento:** un design doc. Non commissiona nessuna implementazione,
non impegna a nessuna scelta. È una lettura critica di una risposta ottenuta dall'utente
in una conversazione separata con ChatGPT sulla stessa idea catturata in
[`docs/improvements.md`](improvements.md) ("Pipeline di sourcing per le lezioni di teoria"),
filtrata contro i vincoli reali di questo progetto. Quando (e se) questo diventerà una
priorità, un design doc vero seguirà lo stesso schema di
[`docs/theory-lessons-design.md`](theory-lessons-design.md) o
[`docs/threatened-pieces-design.md`](threatened-pieces-design.md) — questo file è
materiale di partenza per quel documento, non un sostituto.

**Sull'autore di questo documento:** non sono un avvocato e non ho verificato le licenze
citate qui contro il testo legale originale — ho letto la proposta ChatGPT criticamente
usando quel poco che so e il buon senso, non ho ri-derivato la legge del copyright. Ogni
affermazione di licenza sotto è "quello che mi risulta, da verificare", non un parere
legale. Segnalato esplicitamente dove la mia fiducia è più bassa.

---

## 1. Il punto di partenza — la proposta ChatGPT in sintesi

L'utente ha discusso la stessa idea (lezioni che crescono da fonti open-source, con
citazioni, forse via una "skill" di monitoraggio) in una conversazione ChatGPT separata,
molto più estesa del pitch originale. I punti principali della risposta:

1. Opus dovrebbe essere "compilatore editoriale", non fonte della verità — le fonti
   forniscono materiale grezzo, Opus lo organizza didatticamente, Stockfish/python-chess
   verificano la correttezza scacchistica.
2. L'aggiornamento automatico ha poco valore per la teoria di base (che cambia lentissimo)
   — il valore reale è ampliare il catalogo, non riscrivere lezioni pubblicate.
3. Le fonti non sono equivalenti: propone una triage in 4 categorie di licenza (CC0,
   CC BY-SA, pubblico dominio ma datato, autorevole-ma-non-riusabile).
4. Architettura a tre livelli: source registry → knowledge units → lesson versions
   (immutabili, versionate), con una pipeline "source change → impact analysis →
   candidate → chess validation → editorial review → publication" — **mai auto-publish**.
5. Una skill di monitoraggio dedicata, "Chess Lab Content Pipeline", esplicitamente un
   progetto separato — produce solo candidati, mai pubblica direttamente.
6. Validazione scacchistica non basta se fatta solo con "Stockfish dice che è la mossa
   migliore" — serve verificare che l'esempio dimostri *davvero* il concetto insegnato,
   non solo che la mossa sia forte.
7. Collegamento con `GET /training/weaknesses` (Fase 4): debolezza rilevata → lezione
   proposta → drill/puzzle collegato.
8. MVP consigliato: 20-30 lezioni, fonti registrate, citazioni in fondo, validazione
   automatica FEN/PGN, niente monitoraggio automatico ancora.

---

## 2. Cosa tiene, cosa non tiene — valutazione contro i vincoli reali del progetto

### 2.1 Solido e riusabile così com'è

- **Il principio "propose, don't auto-publish".** Questo è il pezzo di maggior valore
  dell'intera risposta. Anche in miniatura (niente registry, niente knowledge units),
  vale la pena portarselo dietro come vincolo di design non negoziabile per qualunque
  futura versione: se mai un processo automatico legge una fonte, il suo output è
  **sempre** un candidato in un file/branch separato, mai una scrittura diretta a
  `lessons.json` in produzione. Coerente con com'è già strutturato tutto il resto del
  progetto (niente scrittura automatica non supervisionata da nessuna parte — anche le
  migration Alembic sono scritte a mano, mai autogenerate ed eseguite senza revisione).
- **La critica "Stockfish best move ≠ buon esempio didattico".** Punto specifico e
  corretto, e non ovvio finché qualcuno non lo dice esplicitamente. Vale per QUALSIASI
  autoring di lezioni, non solo per contenuto sourced esternamente — si applica già oggi
  alle 5-6 lezioni v1 scritte a mano da Opus/Sonnet in `docs/theory-lessons-design.md`.
  **Raccomandazione indipendente da questo intero progetto di sourcing:** quando si
  autorano lezioni (ora o in futuro), il criterio di validazione "automatica" ragionevole
  resta legalità (FEN valido, mosse legali) + eval non-negativa della mossa "play" — la
  verifica che la posizione dimostri *il tema dichiarato* resta necessariamente una
  scelta editoriale umana (o Opus-in-ruolo-editoriale), non automatizzabile con
  Stockfish da solo. Non serve un intero sistema di sourcing per applicare questa idea:
  si applica già al catalogo v1 statico.
- **Il framing "aggiornamento per ampliare, non per riscrivere".** Osservazione corretta
  e sensata: la teoria elementare (opposizione, forchette, sviluppo) non ha bisogno di
  monitoraggio continuo. Se mai un domani si costruisce qualcosa, questo restringe
  parecchio lo scope utile — non serve "watch this wiki page for diffs", serve piuttosto
  "batch occasionale per trovare 3-5 lezioni nuove", molto più vicino nello spirito a
  come `scripts/build_puzzle_bundle.py` è stato usato in Fase 6 (one-off, manuale,
  rilanciato solo quando si vuole aggiornare il bundle) che a un sistema di monitoraggio
  continuo.
- **Distinzione tra "fonte di conoscenza" e "fonte di verità scacchistica".** Il
  principio che Stockfish/python-chess restano l'autorità sulla correttezza mentre le
  fonti esterne forniscono solo la sostanza narrativa/pedagogica è coerente con come il
  progetto già funziona ovunque altrove (`ENDGAME_DRILLS`, il bundle puzzle, persino le
  lezioni v1 statiche — Stockfish valida, non genera prosa).

### 2.2 Da ridimensionare o semplificare per questa scala di progetto

- **"Source registry / knowledge units / lesson versions" a tre livelli con stati e
  transizioni.** Overengineering evidente per un tool a singolo utente locale senza
  auth, senza account, senza processo editoriale multi-persona. Questo genere di
  pipeline (con uno "stato" `proposed`/`validated`/`published` e uno storico di
  revisioni) ha senso per un prodotto con più autori e un flusso di redazione reale.
  Qui l'"editorial review" è letteralmente l'utente che guarda un JSON prima di un
  commit. **Se mai servisse qualcosa**, la versione proporzionata è: un file di lavoro
  separato (es. `backend/data/lessons_candidates.json` o anche solo un branch git
  dedicato) che un batch di sourcing scrive, e poi un merge manuale in `lessons.json`
  dopo revisione — non tre tabelle/entità con stati e transizioni formali. Il **git
  stesso è già il version control per le lezioni pubblicate** (`lessons.json` versionato
  nel repo) — non serve un secondo sistema di versioning applicativo sopra un file già
  versionato da git.
- **Un `source_registry` con `trust_level`, `sync strategy`, `revision`, `access
  authorization`, ecc.** Per ~5-8 fonti candidate (vedi §3) questo è una tabella di
  metadati enormemente più elaborata del suo contenuto. Una tabella markdown a mano con
  nome/URL/licenza/note (come già fa `pieces/NOTICE.md` e `backend/data/NOTICE.md`) è
  sufficiente e coerente col precedente del progetto — **non serve un DB schema per
  questo**. Se mai l'elenco di fonti crescesse a decine con verifiche periodiche di
  licenza, si potrebbe rivalutare, ma non è lo stato attuale né quello previsto a breve.
- **La "skill di monitoraggio" (MediaWiki `recentchanges`, ecc.) come primo passo.** La
  risposta ChatGPT stessa la mette in coda ("il monitoraggio automatico può arrivare
  dopo"), e concordo pienamente — anzi andrei oltre: per un tool personale con contenuto
  che cambia "lentissimamente" (loro stessa osservazione al punto 2), un batch
  **manuale**, rilanciato dall'utente/da un agente quando si vuole crescere il catalogo
  (stesso pattern one-off di `scripts/build_puzzle_bundle.py`), copre probabilmente
  il 100% del bisogno reale per anni. Un cron/skill che polla MediaWiki per un tool a
  singolo utente locale è una soluzione a un problema di scala che questo progetto non
  ha.
- **20-30 lezioni come "MVP" del sourcing.** Il numero merita un sanity check esplicito
  contro `docs/theory-lessons-design.md`: la v1 (in corso d'implementazione ora, non
  ancora un problema di questo documento) è **~5-6 lezioni**, esplicitamente definita
  come "non una piattaforma di contenuti". Passare a 20-30 è quasi un ordine di
  grandezza in più — ragionevole come orizzonte a lungo termine se l'utente vuole
  davvero un catalogo ricco, ma non è "l'MVP del sourcing", è già una versione matura.
  Se/quando si riprende questo progetto, vale la pena chiedersi esplicitamente quante
  lezioni bastano per un tool che un'unica persona usa per allenarsi — il valore
  marginale della lezione #25 su un tema di apertura specifico è probabilmente basso
  rispetto a più profondità sui drill/puzzle che il sistema già genera dai propri
  errori (Fase 4). Non è chiaro che "più lezioni" sia il vincolo stretto rispetto a,
  per dire, più temi tattici nel profilo debolezze.
- **Validazione "il sistema stabilisce se la posizione è stabile a profondità
  adeguata" ecc.** Buono in linea di principio ma già ridondante con quanto
  `docs/theory-lessons-design.md` §3.1 propone (espansione FEN + validazione di
  legalità via `python-chess`, stesso pattern di `/replay`) — non serve reinventarlo,
  serve solo eventualmente estendere quella validazione esistente con un controllo
  di eval (già discusso al punto "Stockfish best move ≠ buon esempio" sopra).

### 2.3 Sanity-check sui vincoli architetturali del progetto — regge?

Il vincolo più rilevante per questa idea è **"l'app non tocca mai la rete a runtime"**
(vedi Fase 6: `ENDGAME_DRILLS` è statico, `scripts/build_puzzle_bundle.py` è uno script
one-off separato con dipendenze non in `requirements.txt`, il bundle finale è JSON
versionato letto in locale). La proposta ChatGPT rispetta esplicitamente questo vincolo
("il consumo di rete sarebbe in fase di authoring, non runtime") — corretto, e coerente
col precedente. Su questo punto specifico la proposta non ha bisogno di correzioni: il
pattern "script one-off con rete, non nel requirements.txt principale, output JSON
versionato consumato offline" è esattamente il precedente giusto da riusare, non da
reinventare come "source registry" persistente.

Un punto che la proposta ChatGPT **non affronta** e che vale la pena annotare: a
differenza del bundle puzzle Lichess (dati strutturati, JSON, un download via HTTP
Range, un parser), sourcing da Wikibooks/Gutenberg/altro comporta testo in prosa —
molto più lavoro editoriale di riscrittura per farlo entrare nel formato
`lessons.json` (FEN + mosse UCI/SAN + commento breve per step) che non un semplice
parsing strutturato. Il costo reale non è tanto "trovare la fonte" quanto
"trasformare prosa libera in una linea di mosse commentata step-by-step,
verificata legale" — un lavoro editoriale/Opus pesante per lezione, non un fetch
automatizzabile. Questo rafforza ulteriormente l'argomento contro l'automazione
precoce: anche con un source registry perfetto, il collo di bottiglia resta la
riscrittura editoriale umana (o Opus-assistita), non l'individuazione delle fonti.

---

## 3. Triage licenze — cosa mi risulta, con livello di fiducia dichiarato

Nessuna di queste è una consulenza legale. Sono partito dalla lista proposta da ChatGPT
e ho solo verificato la coerenza interna, non ri-derivato il diritto d'autore.

| Fonte | Licenza (mi risulta) | Fiducia | Note |
|-------|----------------------|---------|------|
| `lichess-org/chess-openings` (repo ECO ufficiale) | CC0 | Media-alta — Lichess dichiara pubblicamente CC0 per i suoi dataset, e il progetto **usa già** dati Lichess sotto CC0 per `backend/data/lichess_puzzles.json` (vedi `backend/data/NOTICE.md`) — precedente diretto nello stesso repo. | La più sicura in assoluto, stesso schema già collaudato. |
| Lichess puzzle/game database (CC0) | CC0 | Alta (stesso motivo, già in uso) | Non nuovo per questo progetto. |
| Wikibooks (teoria aperture) | CC BY-SA (verosimilmente 3.0/4.0) | Media — CC BY-SA è effettivamente la licenza standard dei progetti Wikimedia, ma non ho verificato la versione esatta né eventuali eccezioni per singole pagine. | ShareAlike è la clausola scomoda: se presa sul serio implicherebbe che i contenuti derivati (non l'intera app) andrebbero distribuiti con licenza compatibile. Per un progetto **a uso personale, non distribuito** (vedi `pieces/NOTICE.md`: "nessuna distribuzione, nessun uso commerciale" già dichiarato per gli SVG Cburnett GPLv2+) questo vincolo è **oggi senza conseguenze pratiche**, esattamente come per i pezzi. Da tenere presente SOLO se un giorno il progetto smettesse di essere strettamente personale (repo pubblico con licenza dichiarata, distribuzione a terzi, ecc.) — non è la situazione attuale. |
| Project Gutenberg (es. Capablanca, *Chess Fundamentals*) | Pubblico dominio (copyright scaduto) | Media — dipende dal singolo libro/edizione e dalla giurisdizione, Gutenberg lo dichiara per titolo, non ho verificato titolo per titolo. | Il rischio reale qui non è legale ma di **qualità**: linguaggio e valutazioni datate, richiede comunque riscrittura editoriale pesante (vedi §2.3) — non un semplice copia-incolla anche se la licenza lo permettesse. |
| Regolamento FIDE | Non liberamente riusabile/rielaborabile (mi risulta, non verificato a fondo) | Bassa-media — non ho letto i termini FIDE, mi affido al buon senso "un ente regolatore raramente rilascia il proprio testo sotto licenza libera". | Impatto pratico basso: le lezioni di questo progetto sono su strategia/tattica/finali, non regolamento arbitrale — è un non-problema a meno di lezioni tipo "come funziona la patta per tripla ripetizione", dove comunque basterebbe scrivere la spiegazione da zero e citare l'articolo senza copiare il testo. |
| Blog/YouTube/Chess.com/corsi a pagamento | Non riusabile senza permesso esplicito | Alta — questo è senso comune editoriale più che un punto di diritto sottile: contenuto leggibile pubblicamente ≠ licenza di riuso. | Da escludere di default, coerente con la proposta ChatGPT. |

**Osservazione generale sul precedente del progetto:** questo repo ha già gestito la
questione licenze due volte (pezzi Cburnett GPLv2+, bundle Lichess CC0) sempre con lo
stesso pattern minimale — un file `NOTICE.md` con fonte/licenza/nota, nessun sistema di
tracking formale, nessun blocco tecnico. Per un uso strettamente personale la barra
pratica è stata "traccia la provenienza per correttezza, non c'è vincolo legale
stringente" (parole quasi testuali di `pieces/NOTICE.md`). Non c'è motivo evidente per
cui il sourcing delle lezioni dovrebbe avere una barra più alta del sourcing dei
pezzi o dei puzzle — a meno che l'utente non stia pianificando, in futuro, di rendere
il repo pubblico o di distribuirlo, nel qual caso la clausola ShareAlike di Wikibooks
tornerebbe rilevante e andrebbe rivalutata seriamente a quel punto.

---

## 4. Collegamento con `GET /training/weaknesses` — idea buona, non nuova per il progetto

Il collegamento "debolezza rilevata → lezione proposta" è un'estensione naturale di
un pattern che il progetto già ha in forma più debole: `docs/theory-lessons-design.md`
§1 già nota la sequenza pedagogica "lezione → drill/puzzle" come narrativa coerente,
e §6 rimanda esplicitamente "link lezione→drill" a un post-v1 a basso costo. L'idea
ChatGPT di chiudere il cerchio anche dal lato opposto (weakness → lezione, non solo
lezione → drill) è un'estensione legittima e a basso costo **una volta che esiste un
catalogo abbastanza ampio da avere una lezione per ogni `theme` che
`GET /training/weaknesses` può riportare** (`fork`/`pin`/`king_safety` oggi — vedi
CLAUDE.md). Con 5-6 lezioni v1 questo mapping sarebbe quasi vuoto; diventa utile
solo a catalogo cresciuto, quindi è ragionevolmente **posteriore** a un primo giro di
crescita del catalogo, non un prerequisito.

---

## 5. Cosa manca del tutto nella proposta ChatGPT (non affrontato, vale la pena annotare)

- **Nessuna stima di sforzo/tempo.** Tutta la risposta è architetturale, zero numeri —
  a differenza di come questo progetto stima sempre ore per fase (vedi CLAUDE.md e
  `docs/theory-lessons-design.md` §7). Un futuro design doc dovrà aggiungere questa
  stima da zero.
- **Nessuna menzione del formato `lessons.json` già scelto** (in `mode`:
  `show`/`play`, commento per step, espansione FEN via `python-chess` lato backend —
  `docs/theory-lessons-design.md` §3-4). Sensato: la conversazione ChatGPT era
  probabilmente più a monte, sul sourcing in astratto, non sul formato già deciso qui.
  Un futuro design doc dovrà comunque riconciliare esplicitamente "fonte esterna →
  quale sottoinsieme dei campi del formato lezione compila" (es. una fonte tipicamente
  dà una linea di mosse e una spiegazione generale, ma i marcatori `mode: play` per le
  mosse-chiave da indovinare sono una scelta editoriale che nessuna fonte esterna
  fornisce già pronta — è lavoro Opus, non estraibile).
- **Nessuna discussione su duplicati/sovrapposizioni** — se il catalogo cresce nel
  tempo attingendo a più fonti, come si evita di finire con due lezioni quasi identiche
  sull'Italiana prese da fonti diverse? Semplice con 5-6 lezioni, reale a 20-30.

---

## 6. Domande aperte per un futuro design doc

1. **Quante lezioni bastano davvero per un tool a singolo utente?** 20-30 è
   probabilmente più di quanto serva nel breve termine (vedi §2.2) — da ridiscutere con
   l'utente quando la v1 statica è stata usata per un po' e si ha un senso reale di
   "mi manca contenuto" vs "ho abbastanza".
2. **Batch manuale vs skill di monitoraggio** — dato che la teoria di base cambia
   lentissimo (osservazione condivisa con ChatGPT), è plausibile che un batch
   occasionale rilanciato a mano (stile `build_puzzle_bundle.py`) copra il bisogno
   per anni senza mai serva una vera skill di monitoraggio continuo. Da confermare
   quando/se si riprende il progetto.
3. **Qual è davvero il collo di bottiglia?** Se è la riscrittura editoriale (§2.3),
   investire in un elaborato "source registry" prima di aver anche solo provato a
   scrivere 3-4 lezioni sourced a mano da una singola fonte (es. Lichess
   chess-openings per le aperture) sarebbe probabilmente ordine sbagliato — prima
   il proof-of-concept editoriale, poi eventualmente l'infrastruttura se il volume
   lo giustifica.
4. **Wikibooks conviene davvero?** Vista la complicazione ShareAlike (anche se oggi
   senza conseguenze pratiche, §3) contro il fatto che comunque richiede riscrittura
   pesante per il formato lezione, potrebbe non valere la complessità aggiuntiva
   rispetto a scrivere lezioni originali ispirate da fonti CC0/pubblico dominio più
   semplici da maneggiare (Lichess openings, Gutenberg). Da valutare quando si
   sceglieranno le prime fonti reali.
5. **Verifica licenze da fare per davvero, non solo "mi risulta".** Prima di scrivere
   anche solo la prima lezione sourced esternamente, chi implementerà questo dovrà
   verificare le licenze esatte (versione CC BY-SA di Wikibooks, stato pubblico
   dominio del titolo Gutenberg specifico) invece di fidarsi delle affermazioni di
   questo documento o della risposta ChatGPT — nessuna delle due è una fonte legale
   verificata.
6. **Formato del "source_refs" per lezione, se mai implementato.** Anche restando
   minimale (niente registry formale), ogni lezione sourced dovrebbe comunque portare
   almeno `{fonte, url, licenza}` nei suoi metadati (coerente col pitch originale
   dell'utente "ogni lezione con lista di fonti citate") — un campo opzionale in più
   nel JSON esistente, non uno schema nuovo. Dettaglio da chiudere nel design doc
   vero, non qui.

---

## 7. Riassunto in una riga

L'idea di fondo (fonti tracciate, licenza verificata, editorial review, mai
auto-publish, Stockfish come verifica non come fonte) è solida e va tenuta. L'
architettura a tre livelli con registry/knowledge-units/versioning formale è
sproporzionata per un tool a singolo utente locale — la versione giusta per questo
progetto è probabilmente "un batch one-off stile `build_puzzle_bundle.py` + un
`NOTICE.md`-like per le fonti + editing manuale in `lessons.json`", non un sistema.
Resta comunque, come prima, un progetto parallelo non prioritario rispetto alla v1
statica delle lezioni in corso d'implementazione.
