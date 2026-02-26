"""Salesforce API client for pulling contact/lead activity history."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class ActivityRecord:
    """A single task, event, or email from Salesforce."""
    date: str = ""
    activity_type: str = ""  # "Call", "Email", "Meeting", "Task"
    subject: str = ""
    notes: str = ""
    owner: str = ""


@dataclass
class SFContactHistory:
    """Salesforce activity history for a single person."""
    sf_id: str = ""
    sf_object: str = ""  # "Contact" or "Lead"
    name: str = ""
    email: str = ""
    title: str = ""
    company: str = ""
    status: str = ""  # Lead status or Contact stage
    last_activity_date: str = ""
    activities: list[ActivityRecord] = field(default_factory=list)


@dataclass
class SFAccountData:
    """Salesforce Account-level data including opportunities and notes."""
    account_id: str = ""
    account_name: str = ""
    account_owner: str = ""
    account_type: str = ""
    industry: str = ""
    last_activity_date: str = ""
    opportunities: list[dict] = field(default_factory=list)  # list of opp dicts
    notes: list[str] = field(default_factory=list)


class SalesforceClient:
    """Handles Salesforce OAuth and SOQL queries."""

    def __init__(self):
        load_dotenv()
        self.client_id = os.getenv("SF_CLIENT_ID", "")
        self.client_secret = os.getenv("SF_CLIENT_SECRET", "")
        self.username = os.getenv("SF_USERNAME", "")
        self.password = os.getenv("SF_PASSWORD", "")
        self.security_token = os.getenv("SF_SECURITY_TOKEN", "")
        self.instance_url = os.getenv("SF_INSTANCE_URL", "")
        self._access_token: str = ""
        self._base_url: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.username and self.password)

    def authenticate(self) -> bool:
        """Authenticate with Salesforce via OAuth2 password flow."""
        if not self.is_configured:
            logger.info("Salesforce not configured, skipping")
            return False

        login_url = f"{self.instance_url}/services/oauth2/token"
        if not self.instance_url:
            login_url = "https://login.salesforce.com/services/oauth2/token"

        try:
            r = httpx.post(login_url, data={
                "grant_type": "password",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "username": self.username,
                "password": self.password + self.security_token,
            }, timeout=15)
            data = r.json()

            if "access_token" in data:
                self._access_token = data["access_token"]
                self._base_url = data["instance_url"]
                logger.info("Salesforce authenticated: %s", self._base_url)
                return True
            else:
                logger.warning("Salesforce auth failed: %s", data.get("error_description", ""))
                return False
        except Exception as e:
            logger.warning("Salesforce auth error: %s", e)
            return False

    def _query(self, soql: str) -> list[dict]:
        """Execute a SOQL query and return records."""
        if not self._access_token:
            return []
        try:
            r = httpx.get(
                f"{self._base_url}/services/data/v59.0/query/",
                params={"q": soql},
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=15,
            )
            return r.json().get("records", [])
        except Exception as e:
            logger.warning("Salesforce query error: %s", e)
            return []

    def get_contact_history(self, email: str) -> SFContactHistory | None:
        """Look up a person by email and pull their activity history.

        Searches both Contacts and Leads.
        """
        if not self._access_token or not email:
            return None

        # Try Contact first
        records = self._query(
            f"SELECT Id, Name, Email, Account.Name, Title, LastActivityDate "
            f"FROM Contact WHERE Email = '{_escape(email)}' LIMIT 1"
        )
        if records:
            rec = records[0]
            acct = rec.get("Account") or {}
            history = SFContactHistory(
                sf_id=rec["Id"],
                sf_object="Contact",
                name=rec.get("Name", ""),
                email=email,
                title=rec.get("Title", ""),
                company=acct.get("Name", ""),
                last_activity_date=rec.get("LastActivityDate", "") or "",
            )
            self._load_activities(history)
            return history

        # Try Lead
        records = self._query(
            f"SELECT Id, Name, Email, Company, Title, Status, LastActivityDate "
            f"FROM Lead WHERE Email = '{_escape(email)}' LIMIT 1"
        )
        if records:
            rec = records[0]
            history = SFContactHistory(
                sf_id=rec["Id"],
                sf_object="Lead",
                name=rec.get("Name", ""),
                email=email,
                title=rec.get("Title", ""),
                company=rec.get("Company", ""),
                status=rec.get("Status", ""),
                last_activity_date=rec.get("LastActivityDate", "") or "",
            )
            self._load_activities(history)
            return history

        return None

    def _load_activities(self, history: SFContactHistory) -> None:
        """Load tasks and events for a contact/lead."""
        sf_id = history.sf_id

        # Tasks (calls, emails, to-dos)
        tasks = self._query(
            f"SELECT Subject, Description, ActivityDate, Type, CreatedDate, Owner.Name "
            f"FROM Task WHERE WhoId = '{sf_id}' "
            f"ORDER BY CreatedDate DESC LIMIT 15"
        )
        for t in tasks:
            owner = t.get("Owner") or {}
            notes = (t.get("Description") or "").strip()
            # Clean up Teams/email boilerplate
            notes = _clean_notes(notes)
            if not t.get("Subject") and not notes:
                continue
            history.activities.append(ActivityRecord(
                date=t.get("ActivityDate") or (t.get("CreatedDate", ""))[:10],
                activity_type=t.get("Type") or _guess_type(t.get("Subject", "")),
                subject=t.get("Subject", ""),
                notes=notes,
                owner=owner.get("Name", ""),
            ))

        # Events (meetings)
        events = self._query(
            f"SELECT Subject, Description, ActivityDate, Owner.Name "
            f"FROM Event WHERE WhoId = '{sf_id}' "
            f"ORDER BY ActivityDate DESC LIMIT 10"
        )
        for e in events:
            owner = e.get("Owner") or {}
            subject = e.get("Subject", "")
            # Skip canceled events and Teams-only entries
            if subject.lower().startswith("canceled:"):
                continue
            notes = _clean_notes(e.get("Description") or "")
            history.activities.append(ActivityRecord(
                date=e.get("ActivityDate", ""),
                activity_type="Meeting",
                subject=subject,
                notes=notes,
                owner=owner.get("Name", ""),
            ))

        # Sort all by date descending
        history.activities.sort(
            key=lambda a: a.date or "0000",
            reverse=True,
        )

    def get_account_data(self, account_name: str) -> SFAccountData | None:
        """Look up a Salesforce Account by name and pull opportunities + notes.

        Tries exact match first, then LIKE fallback, then normalized name variants.
        """
        if not self._access_token or not account_name:
            return None

        escaped = _escape(account_name)

        # Try exact match first
        records = self._query(
            f"SELECT Id, Name, Owner.Name, Type, Industry, LastActivityDate "
            f"FROM Account WHERE Name = '{escaped}' LIMIT 1"
        )

        # Fallback to LIKE match
        if not records:
            records = self._query(
                f"SELECT Id, Name, Owner.Name, Type, Industry, LastActivityDate "
                f"FROM Account WHERE Name LIKE '%{escaped}%' LIMIT 1"
            )

        # Try with normalized name (stripped legal suffixes)
        if not records:
            normalized = _normalize_firm_name(account_name)
            if normalized != account_name:
                escaped_norm = _escape(normalized)
                records = self._query(
                    f"SELECT Id, Name, Owner.Name, Type, Industry, LastActivityDate "
                    f"FROM Account WHERE Name LIKE '%{escaped_norm}%' LIMIT 1"
                )

        # Try without "The " prefix
        if not records and account_name.lower().startswith("the "):
            without_the = _escape(account_name[4:])
            records = self._query(
                f"SELECT Id, Name, Owner.Name, Type, Industry, LastActivityDate "
                f"FROM Account WHERE Name LIKE '%{without_the}%' LIMIT 1"
            )

        if not records:
            return None

        rec = records[0]
        owner = rec.get("Owner") or {}
        data = SFAccountData(
            account_id=rec.get("Id", ""),
            account_name=rec.get("Name", ""),
            account_owner=owner.get("Name", ""),
            account_type=rec.get("Type", "") or "",
            industry=rec.get("Industry", "") or "",
            last_activity_date=rec.get("LastActivityDate", "") or "",
        )

        account_id = data.account_id

        # Query Opportunities linked to this Account
        # Try with custom Roadblocks__c field first, fall back without it
        opp_fields = (
            "Id, Name, StageName, Amount, CloseDate, Owner.Name, "
            "Probability, Type, NextStep, Description"
        )
        roadblocks_available = True
        opps = self._query(
            f"SELECT {opp_fields}, Roadblocks__c "
            f"FROM Opportunity WHERE AccountId = '{account_id}' "
            f"ORDER BY CloseDate DESC LIMIT 10"
        )
        if not opps:
            # Retry without custom field in case it doesn't exist
            roadblocks_available = False
            opps = self._query(
                f"SELECT {opp_fields} "
                f"FROM Opportunity WHERE AccountId = '{account_id}' "
                f"ORDER BY CloseDate DESC LIMIT 10"
            )
        for opp in opps:
            opp_owner = opp.get("Owner") or {}
            amount = opp.get("Amount")
            amount_str = ""
            if amount is not None:
                try:
                    amount_str = f"${amount:,.0f}"
                except (TypeError, ValueError):
                    amount_str = str(amount)
            desc_raw = _clean_notes(opp.get("Description", "") or "")
            if len(desc_raw) > 500:
                desc_raw = desc_raw[:500].rsplit(" ", 1)[0] + "..."
            roadblocks = ""
            if roadblocks_available:
                roadblocks = (opp.get("Roadblocks__c", "") or "").strip()

            # Fetch notes linked to this specific opportunity
            opp_id = opp.get("Id", "")
            opp_notes = self._fetch_linked_notes(opp_id) if opp_id else []

            data.opportunities.append({
                "name": opp.get("Name", ""),
                "stage": opp.get("StageName", ""),
                "amount": amount_str,
                "close_date": opp.get("CloseDate", "") or "",
                "owner": opp_owner.get("Name", ""),
                "probability": str(opp.get("Probability", "") or ""),
                "opp_type": opp.get("Type", "") or "",
                "next_step": opp.get("NextStep", "") or "",
                "roadblocks": roadblocks,
                "description": desc_raw,
                "opp_notes": opp_notes,
            })

        # Query Notes via ContentDocumentLink → ContentNote
        # First get ContentDocumentIds for SNOTE type
        content_links = self._query(
            f"SELECT ContentDocumentId, ContentDocument.Title "
            f"FROM ContentDocumentLink "
            f"WHERE LinkedEntityId = '{account_id}' "
            f"AND ContentDocument.FileType = 'SNOTE' "
            f"ORDER BY ContentDocument.CreatedDate DESC LIMIT 10"
        )
        for cl in content_links:
            doc_id = cl.get("ContentDocumentId", "")
            doc = cl.get("ContentDocument") or {}
            title = doc.get("Title", "")
            # Fetch full note body via REST API (TextPreview is capped at 255 chars)
            body = self._fetch_note_content(doc_id)
            note_text = f"{title}: {body}" if title and body else title or body
            if note_text:
                data.notes.append(note_text)

        # Fallback to classic Note object if no ContentNotes found
        if not data.notes:
            classic_notes = self._query(
                f"SELECT Title, Body "
                f"FROM Note WHERE ParentId = '{account_id}' "
                f"ORDER BY CreatedDate DESC LIMIT 10"
            )
            for n in classic_notes:
                title = n.get("Title", "")
                body = _clean_notes(n.get("Body", ""))
                note_text = f"{title}: {body}" if title and body else title or body
                if note_text:
                    data.notes.append(note_text)

        logger.info(
            "SF Account '%s': %d opps, %d notes",
            data.account_name, len(data.opportunities), len(data.notes),
        )
        return data

    def _fetch_note_content(self, content_document_id: str) -> str:
        """Fetch the full body of a Salesforce ContentNote via REST API.

        ContentNote.Content is base64-encoded HTML. This decodes it and
        strips HTML tags to return plain text.
        """
        if not self._access_token or not content_document_id:
            return ""
        try:
            import base64
            r = httpx.get(
                f"{self._base_url}/services/data/v59.0/sobjects/ContentNote/{content_document_id}",
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=15,
            )
            data = r.json()
            content_b64 = data.get("Content", "")
            if not content_b64:
                # Fallback to TextPreview if Content not available
                return data.get("TextPreview", "")
            # Decode base64 → HTML → strip tags → plain text
            html_body = base64.b64decode(content_b64).decode("utf-8", errors="replace")
            plain = re.sub(r"<[^>]+>", " ", html_body)
            plain = re.sub(r"&nbsp;", " ", plain)
            plain = re.sub(r"&amp;", "&", plain)
            plain = re.sub(r"&[a-z]+;", " ", plain, flags=re.IGNORECASE)
            plain = re.sub(r"\s+", " ", plain).strip()
            # Limit to 2000 chars for very long notes
            if len(plain) > 2000:
                plain = plain[:2000].rsplit(" ", 1)[0] + "..."
            return plain
        except Exception as e:
            logger.debug("Failed to fetch ContentNote %s: %s", content_document_id, e)
            return ""

    def _fetch_linked_notes(self, entity_id: str) -> list[str]:
        """Fetch notes linked to any Salesforce entity (Opportunity, Account, etc.)."""
        if not self._access_token or not entity_id:
            return []
        notes = []
        try:
            # ContentNote links
            content_links = self._query(
                f"SELECT ContentDocumentId, ContentDocument.Title "
                f"FROM ContentDocumentLink "
                f"WHERE LinkedEntityId = '{entity_id}' "
                f"AND ContentDocument.FileType = 'SNOTE' "
                f"ORDER BY ContentDocument.CreatedDate DESC LIMIT 5"
            )
            for cl in content_links:
                doc_id = cl.get("ContentDocumentId", "")
                doc = cl.get("ContentDocument") or {}
                title = doc.get("Title", "")
                body = self._fetch_note_content(doc_id)
                note_text = f"{title}: {body}" if title and body else title or body
                if note_text:
                    notes.append(note_text)

            # Classic Note objects
            if not notes:
                classic = self._query(
                    f"SELECT Title, Body FROM Note "
                    f"WHERE ParentId = '{entity_id}' "
                    f"ORDER BY CreatedDate DESC LIMIT 5"
                )
                for n in classic:
                    title = n.get("Title", "")
                    body = _clean_notes(n.get("Body", ""))
                    note_text = f"{title}: {body}" if title and body else title or body
                    if note_text:
                        notes.append(note_text)
        except Exception as e:
            logger.debug("Failed to fetch linked notes for %s: %s", entity_id, e)
        return notes

    def bulk_lookup(self, emails: list[str]) -> dict[str, SFContactHistory]:
        """Look up multiple people by email. Returns {email: history}."""
        results = {}
        for email in emails:
            if not email:
                continue
            history = self.get_contact_history(email)
            if history:
                results[email] = history
        return results


def _normalize_firm_name(name: str) -> str:
    """Strip common legal suffixes for fuzzy matching."""
    suffixes = [
        r',?\s*(Inc\.?|LLC|LP|L\.P\.|Ltd\.?|Corp\.?|Corporation|'
        r'Co\.?|Company|Group|Holdings|Partners|Advisors|'
        r'Capital\s+(?:Management|Advisors|Partners)|'
        r'Management|Advisory|Associates|International)\s*$',
    ]
    result = name.strip()
    for pattern in suffixes:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE).strip()
    return result


def _escape(value: str) -> str:
    """Escape single quotes for SOQL."""
    return value.replace("'", "\\'")


def _clean_notes(text: str) -> str:
    """Strip Teams meeting boilerplate and other noise from notes."""
    if not text:
        return ""
    # Skip if it's mostly Teams meeting info
    if "Microsoft Teams" in text and "Join the meeting" in text:
        return ""
    if "Meeting ID:" in text and len(text) < 500:
        return ""
    # Strip email headers if present
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("To:", "CC:", "BCC:", "Attachment:")):
            continue
        if stripped.startswith("Subject:") and not cleaned:
            continue
        if stripped.startswith("Body:") and not cleaned:
            continue
        if stripped == "_" * 40 or stripped.startswith("________"):
            continue
        cleaned.append(line)
    result = "\n".join(cleaned).strip()
    # Limit length
    if len(result) > 2000:
        result = result[:2000].rsplit(" ", 1)[0] + "..."
    return result


def _guess_type(subject: str) -> str:
    """Guess activity type from subject line."""
    s = subject.lower()
    if "call" in s or "outbound" in s or "inbound" in s:
        return "Call"
    if "email" in s:
        return "Email"
    if "meeting" in s or "biweekly" in s or "sync" in s:
        return "Meeting"
    return "Task"
