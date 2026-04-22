# scripts/check_db.py
from app.database import SyncSessionLocal
from app.models.db import Game

def main():
    db = SyncSessionLocal()
    try:
        print("Games:", db.query(Game).count())
        print("✅ DB connection OK")
    finally:
        db.close()


if __name__ == "__main__":
    main()