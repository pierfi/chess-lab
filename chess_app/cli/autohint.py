"""Soglia auto-hint (opt-in), Wave 2 del backlog di design (design doc
docs/cli-companion-mode-design.md §10): invece di mostrare il pannello di
consiglio completo dopo OGNI mossa (comportamento storico di Wave 1, che
resta il default), la modalità silenziosa lo mostra automaticamente solo
quando l'eval della PROPRIA ultima mossa peggiora oltre una soglia
configurabile (es. -150cp) — "ti avviso solo quando stai per sbagliare".

Logica pura, isolata sia dal rendering (`rich` vive solo in ``ui.py``) sia
dalla sessione (``session.py`` orchestra QUANDO chiamarla, non COME
calcola), per essere testabile senza un motore o un terminale veri.

Il calcolo del cp loss riusa ESATTAMENTE la convenzione di segno già
autorevole in ``backend/main.py:analyze_game`` (CLAUDE.md, tabella di
classificazione "centipawn loss dalla parte del giocatore che muove"):

    loss = eval_prima - eval_dopo   (chi ha mosso è il bianco)
    loss = eval_dopo - eval_prima   (chi ha mosso è il nero)

dove ``eval_prima``/``eval_dopo`` sono SEMPRE dal punto di vista del bianco
— stessa convenzione di ``LocalEngineAdvisor.advice()["eval_cp"]``, che
deriva da ``score.white()`` esattamente come ``backend/main.py``. Nessuna
reinvenzione della convenzione di segno: solo il riuso della stessa formula
in un contesto locale (motore della CLI) invece che nel loop di analisi
post-partita lato backend."""

from __future__ import annotations


def move_loss_cp(
    eval_before_white_pov: int | None,
    eval_after_white_pov: int | None,
    mover_color: str,
) -> int | None:
    """Cp persi dalla mossa di ``mover_color``, dal SUO punto di vista —
    stessa formula di ``analyze_game`` in ``backend/main.py``. Un risultato
    positivo significa che la mossa ha peggiorato la posizione per chi l'ha
    giocata; negativo che l'ha migliorata (tipicamente rumore di ricerca a
    depth fissa fra due chiamate separate, non un vero guadagno).

    ``None`` se uno dei due eval non è disponibile (nessuna advice
    "pre-mossa" cachata, tipicamente la primissima mossa di una sessione
    dove il player apre col bianco — nessun consiglio "in avanti" era
    ancora stato mostrato per quella posizione) — il chiamante tratta
    ``None`` come "non quantificabile", mai come un errore o come zero."""
    if eval_before_white_pov is None or eval_after_white_pov is None:
        return None
    if mover_color == "white":
        return eval_before_white_pov - eval_after_white_pov
    return eval_after_white_pov - eval_before_white_pov


def exceeds_threshold(loss_cp: int | None, threshold_cp: int) -> bool:
    """True se la perdita supera la soglia configurata dall'utente (es. 150
    = "avvisami se perdo più di 150cp rispetto al meglio disponibile").

    ``None`` (non quantificabile, vedi ``move_loss_cp``) non supera mai la
    soglia — in assenza di un dato non si forza il pannello completo, si
    degrada silenziosamente al riconoscimento minimo (coerente con lo
    spirito "quiet mode": meno rumore, non un readout paranoico su un dato
    che non abbiamo)."""
    if loss_cp is None:
        return False
    return loss_cp > threshold_cp
