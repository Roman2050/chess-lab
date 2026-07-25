from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    Date,
    DateTime,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


# Analysis lifecycle: pending -> running -> completed | failed.
# A game is claimable by a worker only from these two states: `pending` (never
# analyzed) and `failed` (a previous attempt blew up and may be retried).
ANALYSIS_STATUS_CLAIMABLE = ("pending", "failed")


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)

    # Unique identifier (Lichess ID or SHA hash for custom PGNs)
    # unique=True ensures that there are no duplicates at the database level
    unique_id = Column(String, unique=True, index=True, nullable=False)
    white_player = Column(String, index=True, nullable=False)
    black_player = Column(String, index=True, nullable=False)
    
    # result: "1-0", "0-1", "1/2-1/2"
    result = Column(String, nullable=False) 
    
    # Who won (for quick reference: "White", "Black", "Draw")
    winner = Column(String, index=True) 
    
    # Title of the debut (e.g., "Italian Game: Giuoco Piano")
    opening_name = Column(String, index=True) 
    time_control = Column(String) 
    date_played = Column(Date, index=True)
    
    # A clean PGN file containing only the moves 
    pgn_content = Column(Text, nullable=False)

    analysis_data = Column(JSONB, nullable=True) 

    # Invariant: is_analyzed is True if and only if analysis_status == 'completed'.
    # Both are written in the same transaction by the analysis task.
    is_analyzed = Column(
        Boolean, nullable=False, default=False, server_default=text("false"), index=True
    )

    # pending | running | completed | failed — see ANALYSIS_STATUS_CLAIMABLE
    analysis_status = Column(
        String, nullable=False, default="pending", server_default="pending"
    )
    analysis_started_at = Column(DateTime, nullable=True)
    analysis_error = Column(Text, nullable=True)
    analysis_attempts = Column(Integer, nullable=False, default=0, server_default="0")


    __table_args__ = (
        Index('ix_games_white_winner', 'white_player', 'winner'),
        Index('ix_games_black_winner', 'black_player', 'winner'),
        # Claimable games are a small slice of the table, so the index that the
        # batch fan-out scans stays tiny even as `completed` rows accumulate.
        Index(
            'ix_games_pending_analysis',
            'id',
            postgresql_where=text("analysis_status IN ('pending', 'failed')"),
        ),
    )


class PlayerReport(Base):
    __tablename__ = "player_reports"

    id = Column(Integer, primary_key=True, index=True)

    player_name = Column(String, index=True, nullable=False)
    language = Column(String, nullable=False, default="en", server_default="en")

    # NULL while the first generation is still running
    report_text = Column(Text, nullable=True)

    # Snapshot: how many analyzed games fed into this report
    analyzed_games_count = Column(Integer, nullable=False, default=0, server_default="0")

    # Informational only — never used to decide whether to regenerate
    last_game_played_at = Column(DateTime, nullable=True)

    # ready | generating | failed
    status = Column(String, nullable=False, default="ready", server_default="ready")

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint('player_name', 'language', name='uq_player_reports_player_lang'),
    )