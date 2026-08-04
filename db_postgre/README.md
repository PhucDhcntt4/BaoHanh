# Product catalog database

## 1. Create the local PostgreSQL database

Open PowerShell and run:

```powershell
& "C:\Program Files\PostgreSQL\17\bin\createdb.exe" `
  -U postgres `
  -h 127.0.0.1 `
  -p 5432 `
  baohanh
```

PostgreSQL will ask for the password created during installation.

## 2. Configure the application

Add this line to `.env` and replace `YOUR_PASSWORD`:

```dotenv
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@127.0.0.1:5432/baohanh
```

If the password contains `@`, `:`, `/`, `?` or `#`, URL-encode it first.

## 3. Validate the current JSON without writing data

```powershell
python -m app.scripts.import_products_to_db --dry-run
```

## 4. Create the schema and import the catalog

```powershell
python -m app.scripts.import_products_to_db
```

The importer is idempotent: rerunning it updates matching products,
variants and images instead of recreating the catalog.

## 5. Switch the bot to PostgreSQL

Add this setting to `.env`:

```dotenv
PRODUCT_CATALOG_SOURCE=database
```

Restart Uvicorn and test the product flows. To roll back without deleting
database data, change the setting to:

```dotenv
PRODUCT_CATALOG_SOURCE=json
```
