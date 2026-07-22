"""Entry point per ``python -m chess_app.cli`` (design doc §11.6 — nessun
console-script per questa MVP, un ``__main__.py`` basta)."""

from .repl import main

if __name__ == "__main__":
    main()
