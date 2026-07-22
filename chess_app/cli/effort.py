"""Selezione dell'*effort* utente → forza del motore locale di consiglio.

L'effort mappa SOLO sullo Skill Level del motore locale (design doc §6/§11.2):
mai sulla depth (fissa, vedi ``config.ADVICE_DEPTH``) e mai su un avversario
Stockfish — la companion mode non ha un avversario che gioca, è una partita
osservata. Riusa ``elo_to_skill_depth()`` del backend (stessa tabella ELO→
Skill di CLAUDE.md), scartandone la depth.
"""

from .config import FULL_STRENGTH_ELO, elo_to_skill_depth

# (etichetta, effort_elo). L'ELO è comunque un valore concreto che finisce in
# CompanionNewGameRequest.effort_elo (int obbligatorio 400-2800, persistito in
# games.engine_elo) — anche per "Massimo" serve un numero reale da inviare al
# backend. Il significato "piena forza" per quella fascia si applica SOLO al
# motore locale (vedi skill_level_for_effort), non cambia cosa viene inviato
# al backend.
EFFORT_LEVELS: list[tuple[str, int]] = [
    ("Principiante", 600),
    ("Club", 1200),
    ("Esperto", 1800),
    ("Massimo", FULL_STRENGTH_ELO),
]


def skill_level_for_effort(effort_elo: int) -> int | None:
    """Skill Level per il motore locale, oppure ``None`` per non configurarlo
    affatto (piena forza, default Stockfish). Non è semplicemente "l'ultima
    voce della tabella ELO→Skill": è una scelta esplicita di saltare del
    tutto ``engine.configure()`` per l'effort "Massimo", identica alla ratio
    con cui ``/hint`` tratta un ``hint_elo`` omesso (design doc §6)."""
    if effort_elo >= FULL_STRENGTH_ELO:
        return None
    skill, _ = elo_to_skill_depth(effort_elo)
    return skill


def prompt_effort_choice(input_func=input, output_func=print) -> int:
    """Menu testuale di selezione effort (plain print/input — nessun `rich`
    in questo task, vedi design doc §8). Ritorna l'``effort_elo`` scelto."""
    output_func("Seleziona la forza dei consigli:")
    for i, (label, elo) in enumerate(EFFORT_LEVELS, start=1):
        output_func(f"  {i}. {label} (~{elo} ELO)")
    while True:
        choice = input_func("> ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(EFFORT_LEVELS):
            return EFFORT_LEVELS[int(choice) - 1][1]
        output_func("Scelta non valida, riprova.")
