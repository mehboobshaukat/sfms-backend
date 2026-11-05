# SFMS Backend (Self Finance Management System)

SFMS ek personal finance management system hai jis ka backend **FastAPI + SQLAlchemy + PostgreSQL (Supabase) + Alembic + JWT** par bana hai. Is mein accounts, transactions, goals, credit purchases, analytics aur reports, AI-assisted bulk transaction review, aur ek alag admin layer shamil hain.

## Features

- **Authentication**: JWT based login/register, password hashing, profile management (profile image upload sameet).
- **Accounts & Transactions**: multiple accounts, income/expense/transfer transactions, transaction types (system defaults + user-defined).
- **Goals**: savings goals, goal mein savings add karna aur release karna.
- **Credit Purchases**: credit par khareedari aur uski repayment tracking.
- **Analytics & Reports**: dashboard summaries, charts ke liye data, aur reports.
- **History (Recycle Bin)**: delete karne par live data hat jata hai lekin History mein snapshot rehta hai taake **Undo** ho sake. History entry clear karne ya 30 din guzarne par delete permanent ho jata hai.
- **AI Assistant**: AI se bulk transaction review, reviewed posting, aur validation (setup ke liye `SFMS_AI_ASSISTANT_GUIDE.md` dekhein).
- **Admin Layer**: alag `/api/admin/*` endpoints jin par har request par backend authorization lagti hai.
  - Contact Messages: view, read/unread, archive, delete, reply.
  - Users Management: block/unblock aur related tasks.
  - Admin Management: sirf **Super Admin** admins bana ya hata sakta hai.
  - Admin users ke private financial records nahi dekh sakta.

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

| File | Kis liye |
| --- | --- |
| [`SETUP_GUIDE.md`](./SETUP_GUIDE.md) | Setup, VS Code, PostgreSQL/Supabase connection, packages, deployment |
| [`API_DOCUMENTATION.md`](./API_DOCUMENTATION.md) | Har API endpoint, request/response shape, aur extend karne ka tareeqa |
| [`SFMS_AI_ASSISTANT_GUIDE.md`](./SFMS_AI_ASSISTANT_GUIDE.md) | AI setup, `AI_API_KEY`, reviewed posting aur validation tests |

## Quick Start

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env            # phir .env edit karein (neeche dekhein)
alembic upgrade head
python -m app.utils.seed_system_defaults
uvicorn app.main:app --reload --port 8000
```

Phir browser mein kholein: <http://127.0.0.1:8000/docs>

> **Note:** Agar migrations pehle se `alembic/versions/` mein maujood hain to sirf `alembic upgrade head` chalayein. Naya `alembic revision --autogenerate -m "..."` sirf tab banayein jab models mein tabdeeli ki ho.

## Environment Variables

`.env.example` ko copy kar ke `.env` banayein aur values bharein:

| Variable | Tafseel |
| --- | --- |
| `DATABASE_URL` | Supabase PostgreSQL connection string (`postgresql+psycopg2://...`) |
| `JWT_SECRET_KEY` | Lambi, random, secret key |
| `AI_API_KEY` | AI Assistant ke liye API key |

`.env` file ko kabhi GitHub par push na karein (`.gitignore` mein honi chahiye).

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

- System mein sirf **ek Super Admin** hota hai, jo akela bhi poora system chala sakta hai.
- Super Admin dosray users ko admin bana ya hata sakta hai.
- Normal admins users ko block/unblock kar sakte hain aur contact messages handle kar sakte hain, lekin admins ko create ya remove nahi kar sakte.

## Deployment Notes

Test deployment ke liye yeh cheezein dhyan mein rakhein, khaas kar serverless platforms (jaise Vercel) par:

1. **Startup tasks**: database seeding, user backfill aur expired records purge `app.main` ke startup par chalte hain. Serverless par har cold start par yeh dobara chal sakte hain, is liye inhein alag script/endpoint mein rakhna behtar hai.
2. **Profile images**: `media/profile_images` local filesystem par hain, jo serverless par permanent nahi hota. Persistent storage (jaise Supabase Storage) use karein, ya test ke dauran upload feature band rakhein.
3. **Database connections**: Supabase ka **pooled connection string** use karein aur "too many connections" se bachne ke liye chhota pool (ya `NullPool`) rakhein.
4. **Persistent server platforms** (jaise Render ya Railway) par backend chalana in masail se bachata hai. Free tier ki current shartein deploy se pehle check kar lein.

Mazeed tafseel ke liye `SETUP_GUIDE.md` ka deployment section dekhein.

## Useful Commands

```bash
alembic revision --autogenerate -m "describe change"   # naya migration
alembic upgrade head                                    # migrations apply
alembic downgrade -1                                    # aakhri migration wapas
python -m pip check                                     # dependency compatibility
```
