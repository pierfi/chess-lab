# Chess Lab — Guida all'uso

## Requisiti

- Python 3.13
- Stockfish (`sudo apt-get install stockfish`)

## Setup

```bash
cd chess-lab
source venv/bin/activate
pip install -r requirements.txt
```

## Avvio

```bash
# Terminal 1 — backend
cd chess_app/backend
uvicorn main:app --reload --port 8765

# Terminal 2 — frontend
cd chess_app/frontend
python -m http.server 3000
```

Apri `http://localhost:3000` nel browser.

## Come giocare

1. Scegli il colore (Bianco/Nero) e regola l'ELO di Stockfish con lo slider (400–2800)
2. Clicca **Nuova partita**
3. Clicca un tuo pezzo per selezionarlo — appariranno i dot verdi sulle caselle raggiungibili
4. Clicca una casella con il dot per muovere
5. Stockfish risponde automaticamente
6. Se un pedone raggiunge l'ultima traversa, un popup ti chiede quale pezzo promuovere

## Analisi post-partita

Quando la partita è finita (o in qualsiasi momento), clicca **Analizza partita**. Vedrai:

- **Accuracy %** — percentuale di mosse buone/eccellenti
- **Blunder / Errori / Imprecisioni** — conteggi con colori
- **Lista mosse** — ogni mossa con badge colorato (excellent, good, inaccuracy, mistake, blunder) e perdita in centipawn

## Uso via API (curl)

```bash
# Nuova partita
curl -X POST localhost:8765/game/new \
  -H "Content-Type: application/json" \
  -d '{"player_color":"white","engine_elo":800}'

# Fai una mossa (sostituisci <id> con il game_id ricevuto)
curl -X POST localhost:8765/game/move \
  -H "Content-Type: application/json" \
  -d '{"game_id":"<id>","move_uci":"e2e4"}'

# Stato partita
curl localhost:8765/game/<id>

# Analisi (depth 8–16 consigliato)
curl -X POST localhost:8765/game/analyze \
  -H "Content-Type: application/json" \
  -d '{"game_id":"<id>","depth":14}'
```

## Test

```bash
cd chess_app
pytest tests/ -v
```

## Comandi ELO di riferimento

| ELO | Livello |
|-----|---------|
| 400–800 | Principiante |
| 800–1200 | Intermedio |
| 1200–1600 | Avanzato |
| 1600–2000 | Esperto |
| 2000+ | Maestro |
