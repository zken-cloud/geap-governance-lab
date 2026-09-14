"""partner_tools reads its deployment settings from the environment at import
time (Agent Runtime and deploy.py set them). Give the unit tests stand-ins."""

import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("PARTNER_BASE_URL", "https://partner.example.test")
os.environ.setdefault("AUTH_MANAGER_CONTINUE_URI", "https://guide.example.test/")
