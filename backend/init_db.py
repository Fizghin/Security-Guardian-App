from config import DATABASE_URL
from models.database import init_db

if __name__ == "__main__":
    init_db()
    print(f"Database ready: {DATABASE_URL}")
