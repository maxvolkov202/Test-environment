"""Read CSV/Excel input files and group contacts by company."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from company_research.models import CompanyInput, ContactInfo

# Flexible column name matching
COMPANY_COLUMNS = [
    "company / account",
    "company/account",
    "company name",
    "company",
    "account",
    "firm",
    "organization",
]

PERSON_COLUMNS = [
    "person",
    "person2",
    "contact",
    "name",
    "full name",
    "contact name",
]

# For CSVs with separate first/last name columns (e.g. Apollo exports)
FIRST_NAME_COLUMNS = ["first name", "firstname", "first"]
LAST_NAME_COLUMNS = ["last name", "lastname", "last"]
EMAIL_COLUMNS = ["email", "email address", "work email", "e-mail"]
LINKEDIN_COLUMNS = [
    "person linkedin url", "person linkedin", "linkedin url",
    "linkedin", "linkedin profile",
]

# Legal suffixes — always safe to strip
_LEGAL_SUFFIXES = re.compile(
    r",?\s+(?:Inc\.?|LLC|LP|LLP|L\.P\.|Ltd\.?|Limited|Corporation|Corp\.?|"
    r"Co\.?|Company|PLC|plc|S\.A\.?|GmbH|N\.V\.?)\s*$",
    re.IGNORECASE,
)

# Business descriptors — only strip if the name stays distinctive (2+ words)
_BUSINESS_SUFFIXES = re.compile(
    r"\s+(?:Group|Holdings|Partners|Capital|Management|"
    r"Advisors|Advisory|Investments|Asset Management|"
    r"Private Debt|Private Credit)\s*$",
    re.IGNORECASE,
)


def read_input_file(file_path: str) -> list[CompanyInput]:
    """Read a CSV or Excel file, group rows by company, deduplicate people.

    Returns a sorted list of CompanyInput objects.
    Raises ValueError on unsupported formats or missing columns.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    ext = path.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(path, dtype=str).fillna("")
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    else:
        raise ValueError(
            f"Unsupported file format: {ext}. Use .csv, .xlsx, or .xls"
        )

    if df.empty:
        raise ValueError("Input file is empty")

    company_col, person_col, first_col, last_col = _detect_columns(list(df.columns))

    # Detect optional email and LinkedIn columns
    normalized = {col.strip().lower(): col for col in df.columns}
    email_col = None
    for candidate in EMAIL_COLUMNS:
        if candidate in normalized:
            email_col = normalized[candidate]
            break

    linkedin_col = None
    for candidate in LINKEDIN_COLUMNS:
        if candidate in normalized:
            linkedin_col = normalized[candidate]
            break

    # Group by company, collect unique people with emails
    companies: dict[str, list[ContactInfo]] = {}
    skipped = 0

    for _, row in df.iterrows():
        company = str(row[company_col]).strip()

        # Build person name from single column or first+last
        if person_col:
            person = str(row[person_col]).strip()
        elif first_col and last_col:
            first = str(row[first_col]).strip()
            last = str(row[last_col]).strip()
            person = f"{first} {last}".strip()
        else:
            person = ""

        email = ""
        if email_col:
            email = str(row[email_col]).strip()
            if email.lower() in ("nan", ""):
                email = ""

        linkedin_url = ""
        if linkedin_col:
            linkedin_url = str(row[linkedin_col]).strip()
            if linkedin_url.lower() in ("nan", ""):
                linkedin_url = ""

        if not company or company.lower() in ("unknown", "nan", ""):
            skipped += 1
            continue
        if not person or person.lower() in ("unknown", "nan", ""):
            skipped += 1
            continue

        # Clean up "Unknown" last names (e.g. "Brechnitz Unknown" from CRM exports)
        person = _clean_person_name(person)

        contact = ContactInfo(name=person, email=email, linkedin_url=linkedin_url)

        if company not in companies:
            companies[company] = []
        # Deduplicate by name
        if not any(c.name == person for c in companies[company]):
            companies[company].append(contact)

    if not companies:
        raise ValueError(
            "No valid company-person rows found. "
            f"Checked columns: '{company_col}' and '{person_col or first_col}'"
        )

    result = []
    for company_name, contacts in sorted(companies.items()):
        result.append(
            CompanyInput(
                company_name=company_name,
                search_name=_clean_company_name_for_search(company_name),
                people=[c.name for c in contacts],
                contacts=contacts,
            )
        )

    return result


def _detect_columns(columns: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    """Detect the company and person column names from the dataframe.

    Returns (company_col, person_col, first_name_col, last_name_col).
    person_col is None when first/last name columns are used instead.
    Raises ValueError if required columns cannot be identified.
    """
    normalized = {col.strip().lower(): col for col in columns}

    company_col = None
    for candidate in COMPANY_COLUMNS:
        if candidate in normalized:
            company_col = normalized[candidate]
            break

    person_col = None
    for candidate in PERSON_COLUMNS:
        if candidate in normalized:
            person_col = normalized[candidate]
            break

    # Fall back to first + last name columns (e.g. Apollo exports)
    first_col = None
    last_col = None
    if person_col is None:
        for candidate in FIRST_NAME_COLUMNS:
            if candidate in normalized:
                first_col = normalized[candidate]
                break
        for candidate in LAST_NAME_COLUMNS:
            if candidate in normalized:
                last_col = normalized[candidate]
                break

    errors = []
    if company_col is None:
        errors.append(
            f"Could not find company column. "
            f"Expected one of: {COMPANY_COLUMNS}. "
            f"Found: {columns}"
        )
    if person_col is None and (first_col is None or last_col is None):
        errors.append(
            f"Could not find person column. "
            f"Expected one of: {PERSON_COLUMNS} or "
            f"first/last name columns. "
            f"Found: {columns}"
        )

    if errors:
        raise ValueError("\n".join(errors))

    return company_col, person_col, first_col, last_col


def _clean_company_name_for_search(name: str) -> str:
    """Strip corporate suffixes for cleaner search queries.

    Two-tier approach:
    - Legal suffixes (Inc, LLC, LP, etc.) are always stripped.
    - Business descriptors (Capital, Management, etc.) are only stripped
      if the result still has 2+ words, to avoid overly generic names
      like "Churchill" from "Churchill Asset Management".
    """
    # Always strip legal suffixes (apply twice for "... Partners LLC")
    cleaned = _LEGAL_SUFFIXES.sub("", name).strip()
    cleaned = _LEGAL_SUFFIXES.sub("", cleaned).strip()

    # Only strip business suffixes if result stays 2+ words
    candidate = _BUSINESS_SUFFIXES.sub("", cleaned).strip()
    if len(candidate.split()) >= 2:
        cleaned = candidate
        # Try once more (e.g. "Foo Capital Partners" → "Foo Capital" → keep)
        candidate = _BUSINESS_SUFFIXES.sub("", cleaned).strip()
        if len(candidate.split()) >= 2:
            cleaned = candidate

    return cleaned or name


def _clean_person_name(name: str) -> str:
    """Clean up person names with placeholder parts.

    Handles cases like "Brechnitz Unknown" where the CRM or export
    used "Unknown" as a placeholder for a missing last name.
    """
    parts = name.split()
    # Remove "Unknown" parts (case-insensitive)
    cleaned_parts = [p for p in parts if p.lower() != "unknown"]
    if cleaned_parts:
        return " ".join(cleaned_parts)
    return name
