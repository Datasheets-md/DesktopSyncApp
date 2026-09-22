"""Shared constants. Authentication is now a paste-the-token flow handled in
the GUI + api.py directly. The old password-login round-trip has been removed
in favour of personal API tokens minted in the web UI."""

# The API answers on its own host, not the one serving the web app:
# datasheets.md is the SPA, api.datasheets.md is the REST API (on dev,
# dev.datasheets.md and api-dev.datasheets.md the same way).
API_BASE = "https://api.datasheets.md"
