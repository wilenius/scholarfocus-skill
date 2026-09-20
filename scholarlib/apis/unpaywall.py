"""Unpaywall client: open-access status and full-text locations per DOI.

Free, no key, but the email parameter is required by the API.
"""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.http.base import BaseClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.unpaywall.org/v2"


class UnpaywallClient(BaseClient):
    name = "unpaywall"
    base_url = BASE_URL
    default_ttl = 30 * 86400  # OA status genuinely changes over time

    def __init__(self, email: Optional[str] = None, **kw):
        super().__init__(contact_email=email, **kw)
        self.email = email

    def _available(self) -> bool:
        return bool(self.email)

    def _unavailable_reason(self) -> str:
        return "unpaywall: no email configured (apis.unpaywall.email)"

    def _auth_params(self) -> dict:
        return {"email": self.email} if self.email else {}

    def get_oa(self, doi: str) -> Optional[dict]:
        d = str(doi).replace("https://doi.org/", "").strip()
        return self.get(f"/{d}")

    def oa_for(self, doi: str) -> tuple[Optional[str], Optional[str]]:
        """Return (oa_status, best full-text url)."""
        data = self.get_oa(doi)
        if not data:
            return None, None
        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url")
        return data.get("oa_status"), url
