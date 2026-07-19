# Chess Lab — Miglioramenti

Registro dei miglioramenti non-bug: refinement di feature esistenti, UX, anticipazioni di roadmap.
Formato per voce: pitch / approccio / stato / branch.

---

## Export PGN della partita

**Branch:** `feature/pgn-export`
**Stato:** Implementato, in attesa di merge
**Richiesto da:** utente, 11 luglio 2026

**Pitch:** poter scaricare la partita corrente (in corso o conclusa) come file `.pgn`,
per passarla a tool esterni di analisi (Lichess, ChessBase, SCID, ecc.), come fanno i
maggiori programmi di scacchi.

**Approccio:** il backend costruisce già una stringa PGN completa ad ogni risposta di
stato (`_board_to_state()` in `backend/main.py`, campo `"pgn"`) — nessuna modifica
backend necessaria. Verificato che `chess.pgn.Game()` imposta di default l'header
`Result` a `"*"` (partita in corso/risultato sconosciuto), quindi il PGN di una
partita non ancora finita è già valido secondo spec, importabile così com'è.
Lato frontend: `state.pgn` cattura `data.pgn` ad ogni `updateState()`, un bottone
"Esporta PGN" in toolbar (disabilitato finché non c'è almeno una mossa) triggera
`downloadPgn()`, che crea un `Blob` e un `<a download>` temporaneo per scaricare
`chesslab-<game_id>.pgn`.

**Nota:** puro frontend, nessun nuovo endpoint. Vedi anche la voce Fase 3 in
CLAUDE.md — l'export era già previsto lì solo per l'import; l'export è stato
anticipato qui.

---

## Forza regolabile dell'hint engine (Assisted Mode)

**Branch:** `feature/hint-engine-strength`
**Stato:** Implementato, in attesa di merge
**Richiesto da:** utente, 11 luglio 2026

**Pitch:** in modalità assistita l'hint engine gira sempre a piena forza — i
suggerimenti sono mosse da super-GM, spesso incomprensibili per chi sta imparando.
Poter abbassare la forza dei suggerimenti (non per "barare meno", ma per riceverli
calibrati vicino al proprio livello) trasforma l'Assisted Mode da readout grezzo
dell'engine a strumento di apprendimento graduato: la mossa suggerita diventa una
mossa che un giocatore del proprio livello potrebbe davvero trovare e capire.

**Approccio:** stesso modello mentale della forza avversario. Backend:
`HintRequest` guadagna `hint_elo: int | None` (stessi bound di `engine_elo`,
400–2800); in `game_hint()`, se presente, si riusa `elo_to_skill_depth()` per il
solo Skill Level (`engine.configure({"Skill Level": skill})` sull'istanza per-chiamata
— la depth resta governata da `req.depth`, invariata). **Scelta di design chiave:
`hint_elo` omesso/null = nessuna `configure()` = piena forza — il comportamento
storico è il default, chi non tocca il selettore non vede alcuna differenza.**
Frontend: selettore compatto sotto la toolbar (`#hint-strength`), visibile solo in
modalità assistita, che riusa la scala `STRENGTH_ELO` e il pattern `.strength-row`
del modal setup, più un bottone "Max" (= `hint_elo` omesso, selezione di default).
`state.hintElo` entra nel body di `fetchHint()`; il cambio livello azzera l'hint
corrente e rilancia `fetchHint()`, che incrementa `hintSeq` — le risposte in-flight
alla vecchia forza vengono scartate dal meccanismo anti-race già esistente.

**Nota:** verificato live che `hint_elo: 2400` (Skill 20) coincide con l'output a
piena forza e che a Skill 0 gli eval divergono. Aggiorna la nota di Fase 2
"hint-engine a piena forza": resta vero come *default*, ora è opt-out.

---

## Analysis Panel v2 — tabella a due colonne + curva eval

**Pitch:** il pannello di analisi post-partita (`Analizza` → `POST /game/analyze`) mostrava una lista piatta di semimosse, una riga per ply: difficile capire a colpo d'occhio quale colore avesse giocato cosa. Richiesta esplicita dell'utente: riorganizzare in due colonne Bianco | Nero raggruppate per numero di mossa, come una score-sheet PGN. In aggiunta (concordato, non richiesto): curva eval in centipawn sull'intera partita con marker su blunder/errori e click-to-jump verso la riga corrispondente — di fatto un'anticipazione della riga "Grafico eval" di Fase 5.

**Approccio:**
- *Tabella:* `buildAnalysisMovesHtml()` raggruppa `data.moves` per `move_number` (una `Map`, robusta anche a semimosse mancanti: partita chiusa sulla mossa del Bianco → cella Nero vuota; difensivamente anche il caso inverso per future partite da FEN custom). I badge di classificazione passano dalle parole intere ai simboli di annotazione scacchistica (`!` excellent, `✓` good, `?!` inaccuracy, `?` mistake, `??` blunder, con `title` per il nome completo) per stare nella mezza colonna alla larghezza minima della sidebar (280px); colori badge invariati. Expand/collapse della best line preservato con gli stessi id per ply (`best-line-{i}`, `line-chevron-{i}`, `toggleBestLine(i)` invariato); la best line ora è etichettata con la mossa di riferimento (es. "Meglio (12… Qf6):").
- *Curva eval:* `buildEvalChartSvg()` genera SVG inline puro (nessuna libreria, coerente con `renderArrows()`), `viewBox 300×110`, `score_cp` dal punto di vista del Bianco, punto di partenza virtuale a eval 0. Dominio y dinamico: minimo ±300cp (non amplifica il rumore nei pareggi piatti), massimo ±1000cp — i matti (`is_mate_swing`, clampati a ±1000 dal backend) restano appuntati al bordo del grafico senza distorcere la scala. Marker con identità mai affidata al solo colore: blunder = cerchio `--red`, mistake = rombo `--orange`, entrambi con anello bianco di separazione; palette validata con il validator del design-skill (separazione CVD ok; il warn di contrasto dell'arancio è compensato da forma distinta, tooltip e tabella testuale sottostante). Etichette asse in pedoni (+N / 0 / −N), linea dello zero recessiva.
- *Click-to-jump:* ogni ply ha un gruppo cliccabile sulla curva (hit target 16px, tooltip nativo con mossa/eval/classificazione, dot di hover sui ply senza marker). `selectAnalysisPly(i)` accoppia i due sensi: click sulla curva → highlight + scroll della cella nella tabella; click sulla cella → highlight dell'anello sul punto della curva (e toggle della best line se presente).

**Stato:** implementato l'11 luglio 2026. Sanity check: 32 trace-test in node sulle funzioni pure estratte dal file (analisi vuota, numero dispari di semimosse, mate swing, partita senza errori, black-first, clamp del dominio), sintassi dell'intero script verificata. Verifica visiva a browser non possibile in questo ambiente (vedi nota in `bugs.md`), geometria dell'SVG verificata sui numeri.

**Branch:** `feature/analysis-panel-v2`

---

## Scacchiera ridimensionabile (drag-to-resize)

**Stato:** Da scopare/implementare — in coda dopo Fase 5 (`feature/training-ui`), per evitare conflitti di merge (entrambe toccano `frontend/index.html` e la logica di rendering board)
**Richiesto da:** utente, 11 luglio 2026 — ispirato da un salvataggio HTML della scacchiera di analisi Lichess (`chess_app/resources_and_examples/lichass_analysis_board.html`)

**Pitch:** la board attuale ha una dimensione fissa. Su Lichess la scacchiera si può ingrandire/rimpicciolire trascinando una maniglia nell'angolo, comodo su schermi grandi o per adattarsi al proprio layout preferito.

**Riferimento osservato:** nell'HTML salvato, l'elemento chiave è `<cg-resize>` — la maniglia di ridimensionamento di **chessground** (la libreria board di Lichess), un piccolo handle draggable nell'angolo in basso a destra del container `<cg-board>` (`width`/`height` impostati inline in px, es. `391.111px`). Trascinandolo, chessground ricalcola le dimensioni mantenendo il rapporto 1:1 (scacchiera sempre quadrata).

**Approccio proposto (da verificare in implementazione):**
- Handle draggable (nuovo elemento, angolo in basso a destra del container board esistente), `cursor: nwse-resize`.
- Su `mousedown` sull'handle → `mousemove` aggiorna una dimensione in px (letta da `state`, non `localStorage` — vincolo esistente, si resetta ad ogni ricarica pagina), con un min/max ragionevole (es. 320px–800px) per restare leggibile e non rompere il layout.
- La board (CSS grid `<div>` esistente, non canvas — nessun cambio di tecnica di rendering) lega `width`/`height` del container a quella dimensione via variabile CSS o stile inline; le celle scalano proporzionalmente (già oggi presumibilmente in `%`/`fr` nel grid — da verificare, potrebbe servire un piccolo aggiustamento se sono in px fissi).
- Nessun impatto su logica di gioco, hint, replay: è puro CSS/layout, la board resta lo stesso `buildBoardEl()` introdotto in Fase 3, solo il suo container cambia dimensione.

**Nota:** puramente frontend, nessun endpoint coinvolto. Da lanciare come branch dedicato (`feature/board-resize` o simile) dopo che `feature/training-ui` è mergiata, per non toccare `frontend/index.html` in parallelo con quel lavoro.

---

## Pipeline di sourcing per le lezioni di teoria (idea in backlog, non progettata)

**Stato:** idea catturata, nessun design né implementazione — esplicitamente un progetto parallelo, non bloccante per le lezioni di teoria v1 (`docs/theory-lessons-design.md`)
**Richiesto da:** utente, 18 luglio 2026

**Pitch:** le lezioni di teoria v1 sono un set statico curato a mano (~5-6 lezioni, autorate da agenti Opus/Sonnet). L'utente vorrebbe che il set potesse **crescere nel tempo** attingendo a **fonti open-source** reali (stesso spirito di come sono stati sourced gli SVG dei pezzi Cburnett, con licenza/provenienza tracciate in `pieces/NOTICE.md`), con ogni lezione che porta una **lista di fonti** citate. Ha ipotizzato una **skill dedicata** che individui periodicamente fonti online da monitorare per generare nuove lezioni, e modelli (Opus o altri) che riorganizzino/curino il materiale trovato in formato lezione.

**Perché non ora:** tocca terreno non ancora esplorato in questo progetto — selezione di fonti attendibili, verifica di licenza/attribuzione per contenuto testuale/PGN annotato (diverso dal caso SVG, qui il contenuto è "sostanza" scacchistica, non solo asset grafico), eventuale accesso di rete ricorrente (in contrasto con il pattern attuale "l'app non tocca mai la rete a runtime", vedi il bundle puzzle Lichess in Fase 6 — qui però il consumo sarebbe in fase di *authoring*, non a runtime, quindi il vincolo runtime resterebbe rispettato). Prima di qualunque codice serve un documento di design dedicato (stesso schema di `docs/theory-lessons-design.md` o `docs/threatened-pieces-design.md`) quando la feature avrà priorità.

**Nota:** collegata a `docs/theory-lessons-design.md` §8 domanda 1 ("chi autora le lezioni oltre le due d'esempio").
