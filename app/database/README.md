# PostgreSQL setup

The project uses a dedicated PostgreSQL database and login role:

- Database: `paper_review`
- Role: `paper_review_app`
- Extension: `vector` (pgvector)

Run the standard initializer from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\app\scripts\init_postgres.ps1
```

The script is idempotent. It creates the database only when it does not exist,
never drops an existing database, applies `app/database/schema.sql`, grants the
application role the required privileges, and updates the ignored `app/.env`.
If the PostgreSQL administrator password is not supplied through `PGPASSWORD`,
the script prompts for it. An application password is generated locally when
`-AppPassword` is omitted; it is never committed to Git.

Useful options:

```powershell
# Use a specific PostgreSQL installation
powershell -ExecutionPolicy Bypass -File .\app\scripts\init_postgres.ps1 -PgBin E:\Appdata\postgresql\bin

# Use an existing application password and do not modify app/.env
powershell -ExecutionPolicy Bypass -File .\app\scripts\init_postgres.ps1 -AppPassword "..." -UpdateEnv:$false
```

`schema.sql` contains only database objects and can also be applied directly
with `psql` by an administrator. It includes compatibility tables used by the
four audit agents, the AI orchestrator, and the reflection evaluator.

To intentionally replace only the project database after confirming its name,
pass `-RecreateDatabase`; this uses PostgreSQL 13+'s `DROP DATABASE ... WITH
(FORCE)` and does not touch any other database:

```powershell
powershell -ExecutionPolicy Bypass -File .\app\scripts\init_postgres.ps1 -RecreateDatabase
```
