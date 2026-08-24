"""Mint the credential that ``GMAIL_CREDENTIALS_B64`` holds.

Runs Google's OAuth consent flow in a browser, then prints the base64 of the
pickled :class:`~google.oauth2.credentials.Credentials` - one line, ready to paste
into ``.env``. This is the only supported way to produce that value; see
:mod:`app.modules.notifications.email` for what consumes it.

**Run this whenever sending fails with ``invalid_grant``.** That error means Google
has rejected the refresh token itself, not that anything is misconfigured, and the
only fix is a new token. The usual cause is invisible from the error: while the OAuth
consent screen is in **Testing**, Google expires every refresh token it issues after
**seven days**. Publishing the app (Google Auth Platform -> Audience -> Publish app)
stops that, and is worth doing before minting a token you intend to keep - otherwise
this script becomes a weekly chore.

Prerequisites:

1. An OAuth **client ID of type Desktop app** in a Google Cloud project with the
   Gmail API enabled. Download its JSON - that is the ``credentials.json`` below.
   A *service account* key will not work: sending as a user needs a user's consent,
   or domain-wide delegation this transport does not implement.
2. ``google-auth-oauthlib``, which is a dev dependency rather than a runtime one -
   the server never runs a consent flow, only this script does::

       uv sync --group dev

Usage::

    uv run python scripts/mint_gmail_token.py [path/to/credentials.json]

Sign in as the mailbox that should *send* the mail - whatever account the browser
consents with is the account every email will come from, and it must match
``GMAIL_SENDER`` if that is set.

The printed value is a long-lived credential for sending as that mailbox: put it in
``.env`` or a secret store, never in a commit.
"""

from __future__ import annotations

import base64
import pickle
import sys
from pathlib import Path

#: Sending, and nothing else. A token scoped this narrowly cannot read the mailbox
#: it sends from, so a leak costs spam rather than the contents of the inbox.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

DEFAULT_CLIENT_SECRETS = "credentials.json"


def main(argv: list[str]) -> int:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ModuleNotFoundError:
        print(
            "google-auth-oauthlib is not installed. It is a dev dependency, because only\n"
            "this script needs it:\n\n"
            "    uv sync --group dev\n",
            file=sys.stderr,
        )
        return 1

    client_secrets = Path(argv[1] if len(argv) > 1 else DEFAULT_CLIENT_SECRETS)
    if not client_secrets.is_file():
        print(
            f"No OAuth client file at {client_secrets}.\n\n"
            "Download one from Google Cloud Console -> APIs & Services -> Credentials,\n"
            "as an OAuth client ID of type 'Desktop app', then pass its path:\n\n"
            f"    uv run python scripts/mint_gmail_token.py path/to/{DEFAULT_CLIENT_SECRETS}\n",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)

    print(f"Opening a browser to consent as the sending mailbox (scope: {SCOPES[0]}).")
    # `port=0` takes any free port, so this works on a machine already running the
    # backend on 8000. `access_type=offline` with `prompt=consent` is what makes
    # Google return a *refresh* token: without both, a re-consent for an already
    # authorised client returns only an access token, which expires within the hour
    # and cannot be renewed.
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not credentials.refresh_token:
        print(
            "\nGoogle returned no refresh token, so this credential would stop working\n"
            "within the hour. Revoke this app's access at\n"
            "https://myaccount.google.com/permissions and run the script again.\n",
            file=sys.stderr,
        )
        return 1

    blob = base64.b64encode(pickle.dumps(credentials)).decode("ascii")

    print("\n" + "=" * 78)
    print("Add this to .env as a single line (it is a secret - do not commit it):\n")
    print(f"GMAIL_CREDENTIALS_B64={blob}")
    print("=" * 78)
    print(
        "\nThen restart the backend - settings are read once at import, so a running\n"
        "server keeps using the old value.\n\n"
        "If the OAuth consent screen is still in Testing, this token stops working in\n"
        "7 days. Publish the app (Google Auth Platform -> Audience -> Publish app) to\n"
        "keep it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
