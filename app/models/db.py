from sqlalchemy import Column, Integer, String, Text, Boolean, Date, Index
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base

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
    is_analyzed = Column(Boolean, default=False, index=True)


    __table_args__ = (
        Index('ix_games_white_winner', 'white_player', 'winner'),
        Index('ix_games_black_winner', 'black_player', 'winner'),
    )