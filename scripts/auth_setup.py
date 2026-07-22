"""One-time Google Calendar OAuth setup.

Run this once from the project root: python scripts/auth_setup.py
It opens a browser window — sign in, pick the Google account, approve the
Calendar scope — then writes token.pkl, which core/calendar.py reuses
(refreshing it automatically) for every discovery-call booking after that.
"""
import pickle

from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/calendar"],
)
creds = flow.run_local_server(port=0)

with open("token.pkl", "wb") as f:
    pickle.dump(creds, f)

print("Done! token.pkl saved. You can now start the server.")
