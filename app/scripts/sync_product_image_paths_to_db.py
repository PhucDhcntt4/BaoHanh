from app.database.connection import database_connection
from app.scripts.import_products_to_db import sync_local_image_paths


def main() -> None:
    with database_connection() as connection:
        updated = sync_local_image_paths(connection)
    print(f"Local product image paths synchronized: rows={updated}")


if __name__ == "__main__":
    main()
