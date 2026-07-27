"""
Run this ONCE, locally on your own machine (not on Render), to generate your
GOOGLE_REFRESH_TOKEN. See README.md "Gmail Setup" for the Google Cloud Console
steps that come BEFORE running this script (you need a client_secret.json
downloaded from Google Cloud Console first).

Usage:
    python scripts/gmail_oauth_setup.py

This opens a browser window, asks you to log into the Gmail account you want
the agent to send from, and prints the refresh token to your terminal. Copy
that value into your .env file (and into Render's environment variables) as
GOOGLE_REFRESH_TOKEN. You only need to do this once -- the refresh token
doesn't expire under normal use.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.readonly"]

# Path to the client_secret.json you downloaded from Google Cloud Console
CLIENT_SECRETS_FILE = "client_secret.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n--- SUCCESS ---")
    print("Add these to your .env file and to Render's environment variables:\n")
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
