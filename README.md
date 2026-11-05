# SFMS Backend (Self Finance Management System)

SFMS is a personal finance management system. Its backend is built with **FastAPI + SQLAlchemy + PostgreSQL (Supabase) + Alembic + JWT** and covers accounts, transactions, goals, credit purchases, analytics and reports, AI-assisted bulk transaction review, and a separate admin layer.

## Features

- **Authentication**: JWT-based login and registration, password hashing, and profile management (including profile image upload).
- **Accounts & Transactions**: multiple accounts, income/expense/transfer transactions, and transaction types (system defaults plus user-defined).
- **Goals**: savings goals with the ability to add and release savings.
- **Credit Purchases**: purchases made on credit and repayment tracking.
- **Analytics & Reports**: dashboard summaries, chart data, and reports.
- **History (Recycle Bin)**: deleting an item removes the live data but keeps a snapshot in History so it can be restored with **Undo**. Clearing a History entry, or waiting 30 days, makes the deletion permanent.
- **AI Assistant**: AI-powered bulk transaction review, reviewed posting, and validation (see `SFMS_AI_ASSISTANT_GUIDE.md` for setup).
- **Admin Layer**: dedicated `/api/admin/*` endpoints, with backend authorization enforced on every request.
  - Contact Messages: view, mark read/unread, archive, delete, and reply.
  - Users Management: block/unblock users and related tasks.
  - Admin Management: only the **Super Admin** can create or remove admins.
  - Admins cannot see users' private financial records.

## Tech Stack

| Layer | Technology |
| --- | --- |
| API framework | FastAPI |
| ORM | SQLAlchemy |
| Migrations | Alembic |
| Database | PostgreSQL (Supabase) |
| Auth | JWT |
| AI integration | OpenAI API |
| Server | Uvicorn |

## Documentation

| File | Purpose |
| --- | --- |
| [`SETUP_GUIDE.md`](./SETUP_GUIDE.md) | Setup, VS Code, PostgreSQL/Supabase connection, packages, deployment |
| [`API_DOCUMENTATION.md`](./API_DOCUMENTATION.md) | Every API endpoint, request/response shapes, and how to extend them later |
| [`SFMS_AI_ASSISTANT_GUIDE.md`](./SFMS_AI_ASSISTANT_GUIDE.md) | AI setup, `AI_API_KEY`, reviewed posting, and validation tests |

## Quick Start

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env            # then edit .env (see below)
alembic upgrade head
python -m app.utils.seed_system_defaults
uvicorn app.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000/docs>

> **Note:** If migrations already exist in `alembic/versions/`, just run `alembic upgrade head`. Only create a new migration with `alembic revision --autogenerate -m "..."` after you change the models.

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | Supabase PostgreSQL connection string (`postgresql+psycopg2://...`) |
| `JWT_SECRET_KEY` | A long, random, secret key |
| `AI_API_KEY` | API key for the AI Assistant |

Never push your `.env` file to GitHub (make sure it is listed in `.gitignore`).

## Project Structure

```
app/
  main.py            # FastAPI app (entry point: app.main:app)
  core/              # config, database, security
  utils/             # helpers, seed_system_defaults
media/
  profile_images/    # uploaded profile images (local storage)
alembic/             # database migrations
requirements.txt
```

## Admin Setup

- The system has exactly **one Super Admin**, who can run the whole system alone.
- The Super Admin can promote users to admin and remove adminship.
- Regular admins can block/unblock users and handle contact messages, but cannot create or remove other admins.

## Deployment Notes

Keep the following in mind for a test deployment, especially on serverless platforms such as Vercel:

1. **Startup tasks**: database seeding, user backfill, and purging of expired records run at `app.main` startup. On serverless platforms they may repeat on every cold start, so it is better to move them into a separate script or endpoint.
2. **Profile images**: `media/profile_images` lives on the local filesystem, which is not persistent on serverless. Use persistent storage (for example Supabase Storage), or keep uploads disabled during testing.
3. **Database connections**: use Supabase's **pooled connection string** and keep the pool small (or use `NullPool`) to avoid "too many connections" errors.
4. **Persistent server platforms** (such as Render or Railway) avoid these issues. Check their current free-tier terms before deploying.

See the deployment section of `SETUP_GUIDE.md` for more detail.

## Useful Commands

```bash
alembic revision --autogenerate -m "describe change"   # create a new migration
alembic upgrade head                                    # apply migrations
alembic downgrade -1                                    # roll back the last migration
python -m pip check                                     # check dependency compatibility
```
