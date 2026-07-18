# backend/data — provenienza e licenze

## `lichess_puzzles.json`

Sottoinsieme curato (~400 puzzle) del **Lichess puzzle database**
(<https://database.lichess.org/#puzzles>), rilasciato da Lichess in
**CC0 (public domain)** — nessun vincolo di attribuzione, che forniamo comunque
per correttezza (campo `url` per ogni puzzle: la partita Lichess di origine).

Generato una tantum con `scripts/build_puzzle_bundle.py` (slice iniziale del
CSV ufficiale via HTTP Range, filtri qualità su Popularity/NbPlays/
RatingDeviation, validazione completa di FEN e mosse con python-chess,
campionamento stratificato per fascia di rating). A runtime l'app legge solo
questo file: nessun accesso di rete, nessun download del dataset completo.

Formato di ogni record:

```json
{
  "id": "1dw3d",              // PuzzleId Lichess
  "fen": "...",               // posizione col solutore al tratto (setup move già applicata)
  "initial_uci": "e8e5",      // mossa avversaria che genera la posizione (highlight UI)
  "moves": ["d1d8", "..."],   // soluzione UCI: solutore per primo, lunghezza dispari
  "rating": 410,
  "themes": ["mateIn2", "..."],
  "url": "https://lichess.org/..."
}
```
