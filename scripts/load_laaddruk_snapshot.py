"""One-time snapshot of official RVO/NAL "laaddruk" (charging pressure) per
province, from the NAL Laadinfrastructuur monitor (laadinfrastructuur.databank.nl).

This is NOT an automated recurring ingestion: the monitor's JSON API requires
a per-page-load ephemeral "workspaceGuid" with no documented stable public
endpoint, so a real scraper would need headless-browser automation
(Playwright/Selenium) to rediscover the GUID on every run -- disproportionate
effort for what is officially a slow-changing (~annual) statistic, and no
gemeente-level breakdown exists for laaddruk anyway (provincie is the finest
level RVO publishes for this specific indicator).

Values captured 2026-07-12 from the live dashboard (Provincies_2023 dataset,
variable kb_lda_prv, dimension dnc_type_besch: Rustig / Redelijk rustig /
Redelijk druk / Druk). If this snapshot goes stale, re-capture manually from
https://laadinfrastructuur.databank.nl/mosaic/nl-nl/laadinfrastructuur/laaddruk
(network tab -> GetPresentationAsJson response for the "per Provincie" tile).
"""

import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

# naam, pct_rustig, pct_redelijk_rustig, pct_redelijk_druk, pct_druk
LAADDRUK_SNAPSHOT = [
    ("Groningen", 35.73, 24.50, 14.70, 25.07),
    ("Fryslân", 27.12, 25.99, 16.38, 30.51),
    ("Drenthe", 37.94, 27.37, 19.24, 15.45),
    ("Overijssel", 45.74, 27.66, 13.64, 12.96),
    ("Flevoland", 28.61, 25.67, 13.90, 31.82),
    ("Gelderland", 37.05, 27.00, 15.02, 20.93),
    ("Utrecht", 29.10, 21.19, 15.07, 34.65),
    ("Noord-Holland", 26.48, 19.46, 15.27, 38.79),
    ("Zuid-Holland", 44.46, 22.40, 12.02, 21.11),
    ("Zeeland", 38.46, 26.04, 14.50, 21.01),
    ("Noord-Brabant", 53.55, 22.36, 10.04, 14.06),
    ("Limburg", 62.97, 22.06, 8.18, 6.79),
]

CREATE_TABLE_SQL = """
DROP TABLE IF EXISTS laaddruk_provincie;
CREATE TABLE laaddruk_provincie (
    provincie TEXT PRIMARY KEY,
    pct_rustig DOUBLE PRECISION,
    pct_redelijk_rustig DOUBLE PRECISION,
    pct_redelijk_druk DOUBLE PRECISION,
    pct_druk DOUBLE PRECISION,
    bron TEXT DEFAULT 'RVO / Nationale Agenda Laadinfrastructuur (NAL), Provincies_2023',
    snapshot_date DATE DEFAULT CURRENT_DATE
);
"""


def get_connection():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def main():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        for row in LAADDRUK_SNAPSHOT:
            cur.execute(
                """
                INSERT INTO laaddruk_provincie
                    (provincie, pct_rustig, pct_redelijk_rustig, pct_redelijk_druk, pct_druk)
                VALUES (%s, %s, %s, %s, %s)
                """,
                row,
            )
    conn.commit()
    print(f"Loaded laaddruk snapshot for {len(LAADDRUK_SNAPSHOT)} provinces.")

    with conn.cursor() as cur:
        cur.execute("SELECT provincie, pct_druk FROM laaddruk_provincie ORDER BY pct_druk DESC;")
        for row in cur.fetchall():
            print(f"  {row[0]}: {row[1]}% druk")

    conn.close()


if __name__ == "__main__":
    main()
