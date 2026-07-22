"""Entry point per ``python -m chess_app.cli`` (design doc §11.6 — nessun
console-script per questa MVP, un ``__main__.py`` basta).

Argparse minimale: al momento della scrittura nessun'altra voce Wave 2 aveva
ancora aggiunto un parser qui (né ``--resume`` né input alternativi
FEN/PGN — design doc §10, Wave 2), quindi questo file introduce il primo
parser, scoped al solo flag di questo task (``--auto-hint-threshold``). Se
un'altra branch Wave 2 lo ha nel frattempo aggiunto per un flag diverso, un
merge successivo riconcilierà i due parser in uno solo — atteso, non un
problema di questo task (vedi design doc §10 nota di roadmap)."""

import argparse

from .repl import main


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="chess-lab-companion",
        description="Companion mode CLI per Chess Lab — segui una partita giocata altrove.",
    )
    parser.add_argument(
        "--auto-hint-threshold",
        type=int,
        default=None,
        metavar="CP",
        help=(
            "Modalità silenziosa opt-in (design doc §10, Wave 2): mostra il pannello di consiglio "
            "completo in automatico solo quando la TUA ultima mossa perde più di CP centipawn "
            "rispetto al meglio disponibile; sotto soglia, solo un riconoscimento minimo. "
            "/hint resta sempre disponibile on-demand. Omesso = comportamento storico invariato "
            "(pannello completo dopo ogni mossa, di chiunque)."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(auto_hint_threshold=args.auto_hint_threshold)
