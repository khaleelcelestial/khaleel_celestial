import psycopg2
import psycopg2.errors
from dotenv import load_dotenv
import os

load_dotenv('Q3.env')                                   # loads Q3.env into environment

def run_raw_query():
    conn   = None                                       # initialize so finally block is safe
    cursor = None

    try:
        # ── Step 1: Connect ──────────────────────────────────────────
        database_url = os.getenv("DATABASE_URL")        # read from .env

        if not database_url:
            raise ValueError("DATABASE_URL not found in .env file")

        conn = psycopg2.connect(database_url)           # open connection
        print("Connected to Supabase successfully!")
        print()

        # ── Step 2: Create Cursor & Execute ──────────────────────────
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM payment LIMIT %s;", (5,))  # parameterized ✅

        # ── Step 3: Fetch & Print Results ────────────────────────────
        rows = cursor.fetchall()

        print("Payments (raw SQL):")

        if not rows:
            print("No payments found.")
        else:
            for row in rows:
                print(row)

        print()
        print(f"Rows fetched: {len(rows)}")

    # ── Handle table not existing ─────────────────────────────────────
    except psycopg2.errors.UndefinedTable:
        print("Error: 'payment' table does not exist in the database.")
        print("Please create the table first before querying.")

    # ── Handle wrong credentials / connection failure ─────────────────
    except psycopg2.OperationalError as e:
        print(f"Connection failed: {e}")
        print("Check your DATABASE_URL in the .env file.")

    # ── Handle missing .env value ─────────────────────────────────────
    except ValueError as e:
        print(f"Configuration error: {e}")

    # ── Handle any other database errors ─────────────────────────────
    except psycopg2.Error as e:
        print(f"Database error: {e}")

    # ── Always runs — closes connection cleanly ───────────────────────
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            print("Connection closed.")


run_raw_query()

