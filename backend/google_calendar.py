import os
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from datetime import datetime, timedelta, timezone

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# ============================================================================
# CONFIGURATION
# ============================================================================
CALENDAR_SYNC_DAYS = int(os.environ.get("CALENDAR_SYNC_DAYS", "75"))


def _shift_date(iso_dt):
    """Extract just the calendar date (YYYY-MM-DD) from an ISO datetime string."""
    return datetime.fromisoformat(iso_dt).date().isoformat()


class google_calendar():
    def __init__(self):
        self.token_path = 'token.json'
        creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as err:
                raise Exception(
                    "Google OAuth refresh token is invalid, expired, or has been revoked "
                    "(original error: {}). You need to regenerate token.json locally "
                    "(re-run the OAuth flow / quickstart.py) and update the GOOGLE_TOKEN "
                    "GitHub secret with the new token contents.".format(err)
                )

        self.service = build("calendar", "v3", credentials=creds)
        self.calendar_id = os.environ.get(
            'CALENDAR_ID',
            '9be1390db5471287b61e4bce2393af92c5d2434edab90db3aa96b20554437bf2@group.calendar.google.com'
        )

    def get_upcoming_events(self):
        pass

    def add_event(self, start, end, title, status):
        event = {
            'summary': title,
            'start': {'dateTime': start, 'timeZone': 'Europe/Stockholm'},
            'end': {'dateTime': end, 'timeZone': 'Europe/Stockholm'}
        }
        try:
            result = self.service.events().insert(calendarId=self.calendar_id, body=event).execute()
            return result.get('id')
        except HttpError as err:
            raise Exception(
                "Failed to insert event '{}' ({} -> {}) into calendar {}: {}".format(
                    title, start, end, self.calendar_id, err
                )
            )

    def update_event(self, event_id, start, end, title):
        """Update an existing event's time/title in place (keeps the same event_id)."""
        event = {
            'summary': title,
            'start': {'dateTime': start, 'timeZone': 'Europe/Stockholm'},
            'end': {'dateTime': end, 'timeZone': 'Europe/Stockholm'}
        }
        try:
            self.service.events().update(
                calendarId=self.calendar_id, eventId=event_id, body=event
            ).execute()
        except HttpError as err:
            raise Exception("Failed to update event {}: {}".format(event_id, err))

    def _get_all_events(self):
        """Fetch events within the sync window, keyed by (date, function) -> full event info."""
        events_by_key = {}
        page_token = None

        # Anchor the lower bound to the start of "today" (local time), not the current
        # instant. Otherwise, once a shift's start time passes today, timeMin=now would
        # still include it (it hasn't ended yet) here, but filter_by_window's schedule
        # side used to drop it -- causing it to look "removed" mid-shift. Aligning both
        # windows on start-of-day keeps them consistent.
        start_of_today_local = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        time_min = start_of_today_local.astimezone(timezone.utc).isoformat()
        time_max = (datetime.now(timezone.utc) + timedelta(days=CALENDAR_SYNC_DAYS)).isoformat()

        while True:
            try:
                response = self.service.events().list(
                    calendarId=self.calendar_id,
                    pageToken=page_token,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    maxResults=2500,
                ).execute()
            except HttpError as err:
                raise Exception("Failed to list events from calendar {}: {}".format(self.calendar_id, err))

            for event in response.get('items', []):
                start = event.get('start')
                end = event.get('end')
                summary = event.get('summary')
                if start and end and summary and start.get('dateTime') and end.get('dateTime'):
                    starttime = start.get('dateTime')
                    endtime = end.get('dateTime')
                    key = (_shift_date(starttime), summary)
                    events_by_key[key] = {
                        'event_id': event.get('id'),
                        'starttime': starttime,
                        'endtime': endtime,
                        'summary': summary,
                    }
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        return events_by_key

    def sync_dataframe(self, df):
        """
        Sync scraped shifts to the calendar. Returns a dict with counts and a list
        of change records (added / removed / modified) for downstream notification.
        """
        existing = self._get_all_events()
        seen_keys = set()

        added, deleted, modified, unchanged = 0, 0, 0, 0
        changes = []

        for _, row in df.iterrows():
            if row['status'] != 'Aktiv':
                continue

            starttime, endtime, function = row['starttime'], row['endtime'], row['function']
            key = (_shift_date(starttime), function)
            seen_keys.add(key)

            if key not in existing:
                print("Event added\n", "Starttime:", starttime, "Endtime:", endtime, "Function:", function)
                self.add_event(starttime, endtime, function, row['status'])
                added += 1
                changes.append({
                    'type': 'added',
                    'date': _shift_date(starttime),
                    'function': function,
                    'new_start': starttime,
                    'new_end': endtime,
                })
            else:
                old = existing[key]
                if old['starttime'] != starttime or old['endtime'] != endtime:
                    print("Event modified\n", "Function:", function,
                          "Old:", old['starttime'], "-", old['endtime'],
                          "New:", starttime, "-", endtime)
                    self.update_event(old['event_id'], starttime, endtime, function)
                    modified += 1
                    changes.append({
                        'type': 'modified',
                        'date': _shift_date(starttime),
                        'function': function,
                        'old_start': old['starttime'],
                        'old_end': old['endtime'],
                        'new_start': starttime,
                        'new_end': endtime,
                    })
                else:
                    unchanged += 1

        for key, old in existing.items():
            if key not in seen_keys:
                print("Event deleted\n", "Starttime:", old['starttime'], "Endtime:", old['endtime'], "Function:", old['summary'])
                self.delete_event(old['event_id'])
                deleted += 1
                changes.append({
                    'type': 'deleted',
                    'date': _shift_date(old['starttime']),
                    'function': old['summary'],
                    'old_start': old['starttime'],
                    'old_end': old['endtime'],
                })

        print("Sync summary: {} added, {} modified, {} deleted, {} unchanged".format(
            added, modified, deleted, unchanged
        ))
        return {"added": added, "modified": modified, "deleted": deleted, "unchanged": unchanged, "changes": changes}

    def delete_event(self, event_id):
        if not event_id:
            return
        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as err:
            if err.resp.status == 410:
                print("Event {} already deleted, skipping.".format(event_id))
                return
            raise Exception("Failed to delete event {}: {}".format(event_id, err))
