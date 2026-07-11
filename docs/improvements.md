# Chess Lab — Miglioramenti fuori roadmap

Feature piccole, richieste dall'utente o proposte e approvate, troppo piccole per un
design doc dedicato (vedi `training-mode.md` / `coach-mode.md` per quelle grandi).
Stesso formato leggero di `bugs.md`: pitch, approccio, stato, branch.

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
