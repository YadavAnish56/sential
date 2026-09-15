#!/bin/bash
set -e

# If run from /app, enter backend where alembic.ini and app reside
if [ -d "backend" ] && [ -f "backend/alembic.ini" ]; then
    cd backend
fi

echo "Waiting for PostgreSQL database to be ready..."
# A simple wait mechanism using Python to check the DB connection
python -c "
import sys, time, psycopg2
import os

db_url = os.environ.get('DATABASE_URL')
# simple parse for postgresql+psycopg2
if db_url:
    try:
        # replace postgresql+psycopg2 with postgresql
        clean_url = db_url.replace('postgresql+psycopg2://', 'postgresql://')
        retries = 30
        while retries > 0:
            try:
                conn = psycopg2.connect(clean_url)
                conn.close()
                print('Database is ready.')
                sys.exit(0)
            except psycopg2.OperationalError as e:
                retries -= 1
                time.sleep(1)
        print('Could not connect to database.')
        sys.exit(1)
    except Exception:
        sys.exit(0) # bypass if parsing fails
"

echo "Running Alembic migrations..."
alembic upgrade head

echo "Starting FastAPI server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
