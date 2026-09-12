import dotenv
from os import environ
from pathlib import Path
from datetime import datetime, timedelta
import sys
from backend.softadmin_scraper import softadmin_scraper
from backend.google_calendar import google_calendar
from backend.notifier import notify_changes

BOOKING_WINDOW_DAYS = int(environ.get("BOOKING_WINDOW_DAYS", "75"))


def filter_by_window(df, window_days):
    """Keep only shifts starting within [start of today, now + window_days].

    Uses the start of *today* (not the current instant) as the lower bound so a
    shift scheduled for today isn't dropped from the window (and then wrongly
    reported as "removed" during sync) just because its start time has already
    passed while the shift itself is still valid/ongoing.
    """
    if df.empty:
        return df
    now = datetime.now().astimezone()
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = now + timedelta(days=window_days)
    df = df.copy()
    df["_start_dt"] = df["starttime"].apply(lambda s: datetime.fromisoformat(s))
    filtered = df[(df["_start_dt"] >= start_of_today) & (df["_start_dt"] <= horizon)]
    return filtered.drop(columns=["_start_dt"])


def main():
    current_dir = Path(__file__).resolve().parent
    env_path = current_dir / '.env'
    dotenv.load_dotenv(dotenv_path=env_path)

    username = environ.get("LOGIN_USERNAME")
    password = environ.get("LOGIN_PASSWORD")

    if not username or not password:
        print("FATAL: LOGIN_USERNAME / LOGIN_PASSWORD are not set. Check GitHub secrets.", file=sys.stderr)
        sys.exit(1)

    scr = softadmin_scraper()

    try:
        scr.login(username, password)
        df_schedule = scr.fetch_schedule()
    except Exception as err:
        print("FATAL: scraping Softadmin/PARPAS schedule failed: {}".format(err), file=sys.stderr)
        scr.quit()
        sys.exit(1)
    finally:
        print("Cleaning up driver processes...")
        scr.quit()

    if df_schedule is None or df_schedule.empty:
        print("FATAL: scraper returned no rows at all -- likely a login or page-structure "
              "change, not an empty schedule. Refusing to sync to avoid wiping the calendar.",
              file=sys.stderr)
        sys.exit(1)

    df_schedule = filter_by_window(df_schedule, BOOKING_WINDOW_DAYS)
    print("Scraped {} active-window rows (window = {} days)".format(len(df_schedule), BOOKING_WINDOW_DAYS))

    try:
        cal = google_calendar()
        result = cal.sync_dataframe(df_schedule)
    except Exception as err:
        print("FATAL: Google Calendar sync failed: {}".format(err), file=sys.stderr)
        sys.exit(1)

    print("Done. {} added, {} modified, {} deleted.".format(
        result["added"], result["modified"], result["deleted"]
    ))

    notify_changes(result["changes"])


if __name__ == "__main__":
    main()
