"""Persistenza SQLite per Chess Lab (Fase 3).

Architettura write-through cache:
- Gli oggetti ``chess.Board`` vivi restano nella cache in-memory ``games`` di
  ``main.py`` (hot path — un Board non è serializzabile in DB).
- Il DB SQLite è la fonte durevole: le righe ``moves`` (UCI in ordine di ply)
  sono la verità da cui ricostruire una partita dopo un riavvio del server.
- Su cache-miss (es. dopo un restart) la partita viene ricostruita rigiocando
  gli UCI dal ``games.start_fen`` (o dalla posizione iniziale standard).

Vincoli di threading (vedi CLAUDE.md):
- Gli endpoint FastAPI sono ``def`` sincroni e girano nel threadpool: serve
  ``check_same_thread=False`` sulla connessione SQLite.
- WAL + ``foreign_keys=ON`` sono impostati per-connessione via event listener,
  così ogni connessione del pool li applica (robusto rispetto a un solo
  PRAGMA "at startup").
- SQLite è single-writer: sufficiente per un singolo utente locale.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

# Percorso del file DB. Configurabile via env var così i test puntano a un file
# temporaneo isolato (vedi chess_app/conftest.py) senza sporcare il DB reale.
DB_PATH = os.environ.get(
    "CHESS_LAB_DB",
    os.path.join(os.path.dirname(__file__), "chess_lab.db"),
)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """WAL riduce la contesa di lock tra una scrittura /game/move e una lettura
    /hint concorrente; foreign_keys=ON abilita l'enforcement (SQLite lo tiene
    spento di default) necessario per le ON DELETE CASCADE definite sotto."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


def utcnow() -> datetime:
    """UTC naive — evita il deprecato datetime.utcnow() e i warning tz di
    SQLAlchemy sulle colonne DateTime (naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"

    # id = uuid4().hex[:8] (stesso schema in-memory; API/frontend ci dipendono)
    id: Mapped[str] = mapped_column(String(8), primary_key=True)
    player_color: Mapped[str] = mapped_column(String(5), nullable=False)
    engine_elo: Mapped[int] = mapped_column(Integer, nullable=False)

    result: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # checkmate/stalemate/insufficient_material/fifty_moves/threefold_repetition
    result_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Posizione di partenza custom (drill finali, Fase 4). NULL = partita standard.
    start_fen: Mapped[str | None] = mapped_column(Text, nullable=True)

    # play / endgame_drill / import — solo 'play' scritto in questa fase, ma la
    # colonna resta libera (nessun CHECK) così le fasi successive non vengono
    # rifiutate dallo schema.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="play", server_default="play"
    )

    # Snapshot PGN denormalizzato, aggiornato ad ogni persistenza di mossa.
    pgn: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Riepilogo analisi — lasciate NULL qui, popolate da una fase successiva
    # (/game/analyze non è wired in questa fase).
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    player_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    blunders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mistakes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inaccuracies: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Time control (Fase 6). NULL su initial_seconds = partita non a tempo,
    # comportamento storico invariato (nessun clock tracciato/persistito).
    # white_clock_ms/black_clock_ms sono il tempo RIMANENTE, aggiornato ad ogni
    # persistenza di mossa (write-through, stesso pattern di games.pgn) — la
    # verità corrente del clock, non ricavata a posteriori da moves.think_ms.
    initial_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    increment_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    white_clock_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    black_clock_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    moves: Mapped[list["Move"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        order_by="Move.ply",
    )


class Move(Base):
    __tablename__ = "moves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ply: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    color: Mapped[str] = mapped_column(String(5), nullable=False)  # white/black
    uci: Mapped[str] = mapped_column(String(6), nullable=False)
    san: Mapped[str] = mapped_column(String(16), nullable=False)
    # Posizione PRIMA che questo ply fosse giocato: rende banale replay e
    # generazione FEN-puzzle a valle, senza ri-simulare la board.
    fen_before: Mapped[str] = mapped_column(Text, nullable=False)
    # Think time reale in ms (vedi timing in CLAUDE.md). NULL ammesso: prima
    # mossa dopo un restart (marker last_ready_at assente).
    think_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    game: Mapped["Game"] = relationship(back_populates="moves")

    __table_args__ = (
        UniqueConstraint("game_id", "ply", name="uq_moves_game_ply"),
    )


class AnalysisResult(Base):
    """Schema-only in Fase 3 — popolata da una fase successiva a partire da
    /game/analyze. Nessuna logica qui."""

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ply: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    loss_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    score_cp: Mapped[int] = mapped_column(Integer, nullable=False)
    best_move_uci: Mapped[str | None] = mapped_column(String(6), nullable=True)
    is_mate_swing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    __table_args__ = (
        UniqueConstraint("game_id", "ply", name="uq_analysis_game_ply"),
    )


class Puzzle(Base):
    """Schema-only in Fase 3 — puzzle self-generated dai propri errori (Fase 4)."""

    __tablename__ = "puzzles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ply: Mapped[int] = mapped_column(Integer, nullable=False)
    fen: Mapped[str] = mapped_column(Text, nullable=False)
    best_move_uci: Mapped[str] = mapped_column(String(6), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # blunder|mistake
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    __table_args__ = (
        UniqueConstraint("game_id", "ply", name="uq_puzzles_game_ply"),
    )


class SrsCard(Base):
    """Schema-only in Fase 3 — scheduling SM-2 dei puzzle (Fase 4)."""

    __tablename__ = "srs_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    puzzle_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("puzzles.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ease_factor: Mapped[float] = mapped_column(
        Float, nullable=False, default=2.5, server_default=text("2.5")
    )
    correct_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )


@contextmanager
def session_scope():
    """Sessione-per-unità-di-lavoro: commit in uscita, rollback su errore,
    close garantito. Usata inline dagli endpoint sincroni."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Crea le tabelle se non esistono. Comodità per far girare l'app
    stand-alone senza dover lanciare ``alembic upgrade head`` a mano; Alembic
    resta la via formale per le migration (vedi alembic/). Idempotente."""
    Base.metadata.create_all(bind=engine)
