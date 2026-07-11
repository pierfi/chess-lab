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

## Analysis Panel v2 — tabella a due colonne + curva eval

**Pitch:** il pannello di analisi post-partita (`Analizza` → `POST /game/analyze`) mostrava una lista piatta di semimosse, una riga per ply: difficile capire a colpo d'occhio quale colore avesse giocato cosa. Richiesta esplicita dell'utente: riorganizzare in due colonne Bianco | Nero raggruppate per numero di mossa, come una score-sheet PGN. In aggiunta (concordato, non richiesto): curva eval in centipawn sull'intera partita con marker su blunder/errori e click-to-jump verso la riga corrispondente — di fatto un'anticipazione della riga "Grafico eval" di Fase 5.

**Approccio:**
- *Tabella:* `buildAnalysisMovesHtml()` raggruppa `data.moves` per `move_number` (una `Map`, robusta anche a semimosse mancanti: partita chiusa sulla mossa del Bianco → cella Nero vuota; difensivamente anche il caso inverso per future partite da FEN custom). I badge di classificazione passano dalle parole intere ai simboli di annotazione scacchistica (`!` excellent, `✓` good, `?!` inaccuracy, `?` mistake, `??` blunder, con `title` per il nome completo) per stare nella mezza colonna alla larghezza minima della sidebar (280px); colori badge invariati. Expand/collapse della best line preservato con gli stessi id per ply (`best-line-{i}`, `line-chevron-{i}`, `toggleBestLine(i)` invariato); la best line ora è etichettata con la mossa di riferimento (es. "Meglio (12… Qf6):").
- *Curva eval:* `buildEvalChartSvg()` genera SVG inline puro (nessuna libreria, coerente con `renderArrows()`), `viewBox 300×110`, `score_cp` dal punto di vista del Bianco, punto di partenza virtuale a eval 0. Dominio y dinamico: minimo ±300cp (non amplifica il rumore nei pareggi piatti), massimo ±1000cp — i matti (`is_mate_swing`, clampati a ±1000 dal backend) restano appuntati al bordo del grafico senza distorcere la scala. Marker con identità mai affidata al solo colore: blunder = cerchio `--red`, mistake = rombo `--orange`, entrambi con anello bianco di separazione; palette validata con il validator del design-skill (separazione CVD ok; il warn di contrasto dell'arancio è compensato da forma distinta, tooltip e tabella testuale sottostante). Etichette asse in pedoni (+N / 0 / −N), linea dello zero recessiva.
- *Click-to-jump:* ogni ply ha un gruppo cliccabile sulla curva (hit target 16px, tooltip nativo con mossa/eval/classificazione, dot di hover sui ply senza marker). `selectAnalysisPly(i)` accoppia i due sensi: click sulla curva → highlight + scroll della cella nella tabella; click sulla cella → highlight dell'anello sul punto della curva (e toggle della best line se presente).

**Stato:** implementato l'11 luglio 2026. Sanity check: 32 trace-test in node sulle funzioni pure estratte dal file (analisi vuota, numero dispari di semimosse, mate swing, partita senza errori, black-first, clamp del dominio), sintassi dell'intero script verificata. Verifica visiva a browser non possibile in questo ambiente (vedi nota in `bugs.md`), geometria dell'SVG verificata sui numeri.

**Branch:** `feature/analysis-panel-v2`
