import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

conn = psycopg.connect(
    host=os.environ["POSTGRES_HOST"],
    port=os.environ["POSTGRES_PORT"],
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
)

with conn.cursor() as cur:
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cur.execute("SELECT PostGIS_Version();")
    print("PostGIS version:", cur.fetchone()[0])
    conn.commit()

conn.close()
print("Connection successful.")
