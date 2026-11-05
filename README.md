# SFMS Backend (Self Finance Management System)

FastAPI + SQLAlchemy + PostgreSQL (Supabase) + Alembic + JWT backend, built directly from
`SFMS_Backend_Architecture.md`.

- **Setup, VS Code, PostgreSQL/Supabase connection, packages, deployment** → see [`SETUP_GUIDE.md`](./SETUP_GUIDE.md)
- **Every API endpoint, request/response shape, and how to extend it later** → see [`API_DOCUMENTATION.md`](./API_DOCUMENTATION.md)

## Quick start
```bash
python -m venv venv
source venv/bin/activate        # venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
cp .env.example .env            # then edit .env with your Supabase DATABASE_URL + a real JWT_SECRET_KEY
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
python -m app.utils.seed_system_defaults
uvicorn app.main:app --reload --port 8000
```
Then open http://127.0.0.1:8000/docs

## AI bulk transaction review

See `SFMS_AI_ASSISTANT_GUIDE.md` for backend setup, `AI_API_KEY`, reviewed posting and validation tests.
