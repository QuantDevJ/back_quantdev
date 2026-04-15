# Quantly Setup

## 1) Virtual environment (Python 3.11+)
```bash
cd /Users/zishanccript/Desktop/quantly
/opt/homebrew/bin/python3.11 -m venv venv
source venv/bin/activate
```

## 2) Install packages
```bash
pip install -r requirements.txt
```

## 3) Environment
```bash
cp .env.example .env
```
Edit `.env` with your database credentials.

## 4) Migrations
From repo root:
```bash
alembic upgrade head
```

## 5) Run API
```bash
uvicorn main:app --reload
```
