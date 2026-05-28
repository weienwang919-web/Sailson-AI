from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from app.database import SessionLocal, init_db
from app.services.import_service import import_workbook

load_dotenv(Path(__file__).with_name(".env"))

SOURCES = [
    Path("/Users/brucewayne/Downloads/【Sailson】HOK_印尼站KOL名单_0520.xlsx"),
    Path("/Users/brucewayne/Downloads/list/list.xlsx"),
    Path("/Users/brucewayne/Downloads/Sailson SMS_KOL List.xlsx"),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for source in SOURCES:
            if source.exists():
                result = import_workbook(db, source, source.name)
                print(f"{source.name}: {result}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
