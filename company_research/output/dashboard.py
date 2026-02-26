"""HTML dashboard generator — sidebar layout, SD-branded green theme,
CRM Account/Opportunities/Notes, person accordions with timeline."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path

from company_research.models import CompanyResult, PersonProfile


def _domain_from_url(url: str) -> str:
    """Extract a readable domain label from a URL."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.removeprefix("www.")
        return host or url[:40]
    except Exception:
        return url[:40]


def _linkify_sources(text: str, source_urls: list[str]) -> str:
    """Replace [Source N] with clickable links, or strip them if no sources."""
    escaped = html.escape(text)
    if not source_urls:
        # Strip [Source N] markers cleanly when sources aren't available
        return re.sub(r'\s*\[Source \d+\]', '', escaped)
    def replacer(m):
        n = int(m.group(1))
        if 1 <= n <= len(source_urls):
            url = html.escape(source_urls[n - 1])
            return f'<a href="{url}" class="source-link" target="_blank" title="{url}">[{n}]</a>'
        return m.group(0)
    return re.sub(r'\[Source (\d+)\]', replacer, escaped)


def _sort_news_reverse_chrono(items: list[str]) -> list[str]:
    """Sort news items by date descending, extracting dates from item text."""
    MONTHS = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    QUARTERS = {"q1": 2, "q2": 5, "q3": 8, "q4": 11}

    def _extract_sort_key(item: str) -> tuple[int, int]:
        text = item.lower().strip()
        qm = re.match(r"(q[1-4])\s+(\d{4})", text)
        if qm:
            return (int(qm.group(2)), QUARTERS.get(qm.group(1), 6))
        mm = re.match(r"(\w+)\s+(\d{4})", text)
        if mm and mm.group(1) in MONTHS:
            return (int(mm.group(2)), MONTHS[mm.group(1)])
        ym = re.search(r"(\d{4})\s*-\s*(\d{4})", text)
        if ym:
            return (int(ym.group(2)), 6)
        ym2 = re.match(r"(?:approximately\s+)?(\d{4})\s", text)
        if ym2:
            return (int(ym2.group(1)), 6)
        return (0, 0)

    return sorted(items, key=_extract_sort_key, reverse=True)


def generate_dashboard(
    companies: list[CompanyResult],
    output_path: str,
) -> str:
    """Generate the interactive HTML dashboard and write to disk."""
    metadata = _build_metadata(companies)
    sidebar_items = "\n".join(
        _render_sidebar_item(company, idx) for idx, company in enumerate(companies)
    )
    detail_panels = "\n".join(
        _render_detail_panel(company, idx) for idx, company in enumerate(companies)
    )
    summary_stats = _render_summary_stats(metadata)

    # Serialize company data for JS sorting/filtering
    companies_json = json.dumps([
        {
            "idx": idx,
            "name": c.company.company_name,
            "fitScore": c.fit_score.total,
            "fitRating": c.fit_score.rating,
            "peopleCount": len(c.company.people),
            "industries": ",".join(c.intelligence.investment_strategy.industry_focus),
            "hasOpps": bool(c.sf_account and c.sf_account.opportunities),
        }
        for idx, c in enumerate(companies)
    ])

    dashboard_html = _TEMPLATE.format(
        generated_at=datetime.now().strftime("%A, %B %d, %Y at %I:%M %p"),
        summary_stats=summary_stats,
        sidebar_items=sidebar_items,
        detail_panels=detail_panels,
        companies_json=companies_json,
    )

    Path(output_path).write_text(dashboard_html, encoding="utf-8")
    return output_path


def _build_metadata(companies: list[CompanyResult]) -> dict:
    return {
        "total_companies": len(companies),
        "total_contacts": sum(len(c.company.people) for c in companies),
        "high_fit": sum(1 for c in companies if c.fit_score.rating == "High"),
        "medium_fit": sum(1 for c in companies if c.fit_score.rating == "Medium"),
        "low_fit": sum(1 for c in companies if c.fit_score.rating == "Low"),
        "with_news": sum(
            1 for c in companies
            if (c.intelligence.recent_activity.fund_raisings
                or c.intelligence.recent_activity.acquisitions
                or c.intelligence.recent_activity.major_announcements
                or c.intelligence.recent_activity.partnerships)
        ),
        "persons_researched": sum(
            1 for c in companies
            for p in c.person_profiles
            if p.current_title or p.prior_experience or p.education
        ),
    }


def _render_summary_stats(meta: dict) -> str:
    return f"""
    <div class="summary-cards">
      <div class="stat-card">
        <div class="stat-value">{meta['total_companies']}</div>
        <div class="stat-label">Companies</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{meta['total_contacts']}</div>
        <div class="stat-label">Contacts</div>
      </div>
      <div class="stat-card stat-high">
        <div class="stat-value">{meta['high_fit']}</div>
        <div class="stat-label">High Fit</div>
      </div>
      <div class="stat-card stat-medium">
        <div class="stat-value">{meta['medium_fit']}</div>
        <div class="stat-label">Medium Fit</div>
      </div>
      <div class="stat-card stat-low">
        <div class="stat-value">{meta['low_fit']}</div>
        <div class="stat-label">Low Fit</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{meta['with_news']}</div>
        <div class="stat-label">With News</div>
      </div>
      <div class="stat-card stat-intel">
        <div class="stat-value">{meta['persons_researched']}</div>
        <div class="stat-label">Person Intel</div>
      </div>
    </div>"""


def _render_sidebar_item(company: CompanyResult, idx: int) -> str:
    name = html.escape(company.company.company_name)
    score = company.fit_score.total
    rating = company.fit_score.rating.lower()
    people_count = len(company.company.people)

    # Check if intelligence is empty
    intel = company.intelligence
    has_data = (
        intel.company_overview.company_type
        or intel.company_overview.aum
        or intel.investment_strategy.lending_types
        or intel.investment_strategy.deal_types
    )

    # CRM indicator dot
    crm_dot = ""
    if any(p.interactions for p in company.person_profiles):
        crm_dot = '<span class="sidebar-crm-dot" title="Has CRM history"></span>'

    # Opportunity dollar indicator
    opp_indicator = ""
    if company.sf_account and company.sf_account.opportunities:
        opp_count = len(company.sf_account.opportunities)
        opp_indicator = f'<span class="sidebar-opp" title="{opp_count} opportunities">$</span>'

    # No-data warning
    warn_indicator = ""
    if not has_data:
        warn_indicator = '<span class="sidebar-warn" title="Insufficient data — re-run with --force-refresh">!</span>'

    return (
        f'<div id="sidebar-{idx}" class="sidebar-item" data-idx="{idx}" '
        f'onclick="selectCompany({idx})">'
        f'<div class="sidebar-item-top">'
        f'<span class="sidebar-score fit-{rating}">{score}</span>'
        f'<span class="sidebar-name">{name}</span>'
        f'</div>'
        f'<div class="sidebar-item-bottom">'
        f'<span class="sidebar-people">{people_count} contact{"s" if people_count != 1 else ""}</span>'
        f'{crm_dot}{opp_indicator}{warn_indicator}'
        f'</div>'
        f'</div>'
    )


def _render_detail_panel(company: CompanyResult, idx: int) -> str:
    c = company
    intel = c.intelligence
    overview = intel.company_overview
    recent = intel.recent_activity
    strategy = intel.investment_strategy
    criteria = intel.investment_criteria
    portfolio = intel.portfolio_highlights
    summary = c.summary
    fit = c.fit_score

    name = html.escape(c.company.company_name)

    # --- FRESHNESS BADGE ---
    freshness_badge = ""
    try:
        ts = datetime.fromisoformat(c.processed_at)
        age_label = ts.strftime("%b %d, %Y")
        if c.from_cache:
            freshness_badge = f'<span class="freshness-badge cached" title="Loaded from cache">Cached {age_label}</span>'
        else:
            freshness_badge = f'<span class="freshness-badge fresh" title="Freshly researched">Researched {age_label}</span>'
    except Exception:
        pass

    # --- HERO ---
    meta_items = []
    if overview.aum:
        aum_label = "Private Credit AUM" if overview.aum_type == "Private Credit" else "Total AUM" if overview.aum_type == "Total" else "AUM"
        meta_items.append(f'<div class="hero-stat"><span class="hero-stat-val">{html.escape(overview.aum)}</span><span class="hero-stat-lbl">{aum_label}</span></div>')
    if overview.company_type:
        meta_items.append(f'<div class="hero-stat"><span class="hero-stat-val">{html.escape(overview.company_type)}</span><span class="hero-stat-lbl">Type</span></div>')
    if overview.founded:
        meta_items.append(f'<div class="hero-stat"><span class="hero-stat-val">{html.escape(overview.founded)}</span><span class="hero-stat-lbl">Founded</span></div>')
    if overview.headquarters:
        meta_items.append(f'<div class="hero-stat"><span class="hero-stat-val">{html.escape(overview.headquarters)}</span><span class="hero-stat-lbl">HQ</span></div>')
    if overview.employees:
        meta_items.append(f'<div class="hero-stat"><span class="hero-stat-val">{html.escape(overview.employees)}</span><span class="hero-stat-lbl">Employees</span></div>')
    meta_html = "\n".join(meta_items)

    fit_class = f"fit-{fit.rating.lower()}"

    # --- CRM ACCOUNT BAR ---
    account_bar_html = ""
    sf = c.sf_account
    if sf:
        bar_items = []
        if sf.account_owner:
            bar_items.append(f'<div class="acct-bar-item"><span class="acct-bar-label">Owner</span><span class="acct-bar-value">{html.escape(sf.account_owner)}</span></div>')
        if sf.account_type:
            bar_items.append(f'<div class="acct-bar-item"><span class="acct-bar-label">Type</span><span class="acct-bar-value">{html.escape(sf.account_type)}</span></div>')
        if sf.last_activity_date:
            bar_items.append(f'<div class="acct-bar-item"><span class="acct-bar-label">Last Activity</span><span class="acct-bar-value">{html.escape(sf.last_activity_date)}</span></div>')
        if sf.opportunities:
            bar_items.append(f'<div class="acct-bar-item"><span class="acct-bar-label">Opportunities</span><span class="acct-bar-value">{len(sf.opportunities)}</span></div>')
        if sf.notes:
            bar_items.append(f'<div class="acct-bar-item"><span class="acct-bar-label">Notes</span><span class="acct-bar-value">{len(sf.notes)}</span></div>')
        if bar_items:
            account_bar_html = f'<div class="account-bar"><div class="acct-bar-title">Salesforce Account</div><div class="acct-bar-items">{"".join(bar_items)}</div></div>'

    # --- INSUFFICIENT DATA WARNING ---
    data_warning_html = ""
    has_intelligence = (
        overview.company_type
        or overview.aum
        or strategy.lending_types
        or strategy.deal_types
        or recent.fund_raisings
        or recent.major_announcements
        or portfolio.recent_deals
    )
    if not has_intelligence:
        error_detail = html.escape(c.error) if c.error else "Intelligence extraction returned no data"
        data_warning_html = (
            f'<div class="data-warning">'
            f'<div class="data-warning-icon">!</div>'
            f'<div class="data-warning-text">'
            f'<strong>Insufficient Data</strong> — {error_detail}. '
            f'Try re-running with <code>--force-refresh --company &quot;{name}&quot;</code>'
            f'</div></div>'
        )

    # --- TALKING POINTS ---
    talking_points_html = ""
    tp_parts = []
    if summary.overview:
        first_sent = summary.overview.split(". ")[0] + "."
        tp_parts.append(html.escape(first_sent))
    if summary.credit_focus:
        first_sent = summary.credit_focus.split(". ")[0] + "."
        tp_parts.append(html.escape(first_sent))
    if summary.notable_details:
        first_sent = summary.notable_details.split(". ")[0] + "."
        tp_parts.append(html.escape(first_sent))
    if tp_parts:
        tp_items = "".join(f'<li>{p}</li>' for p in tp_parts)
        talking_points_html = f'<div class="talking-points"><div class="tp-header">Talking Points</div><ul>{tp_items}</ul></div>'

    # --- OPPORTUNITIES TABLE ---
    opps_html = ""
    if sf and sf.opportunities:
        rows = ""
        for opp in sf.opportunities:
            # Type badge
            type_badge = ""
            if opp.opp_type:
                type_badge = f' <span class="opp-type-badge">{html.escape(opp.opp_type)}</span>'
            rows += (
                f'<tr>'
                f'<td>{html.escape(opp.name)}{type_badge}</td>'
                f'<td><span class="opp-stage">{html.escape(opp.stage)}</span></td>'
                f'<td class="opp-amount">{html.escape(opp.amount)}</td>'
                f'<td>{html.escape(opp.close_date)}</td>'
                f'<td>{html.escape(opp.owner)}</td>'
                f'</tr>'
            )
            # Details sub-row: next_step, roadblocks, description, opp notes
            detail_parts = []
            if opp.next_step:
                detail_parts.append(f'<div class="opp-detail"><strong>Next Step:</strong> {html.escape(opp.next_step)}</div>')
            if opp.roadblocks:
                detail_parts.append(f'<div class="opp-detail opp-roadblock"><strong>Roadblocks:</strong> {html.escape(opp.roadblocks)}</div>')
            if opp.description:
                detail_parts.append(f'<div class="opp-detail">{html.escape(opp.description)}</div>')
            if opp.opp_notes:
                for note in opp.opp_notes:
                    detail_parts.append(f'<div class="opp-detail opp-note-item">{html.escape(note)}</div>')
            if detail_parts:
                details_content = "".join(detail_parts)
                rows += (
                    f'<tr class="opp-notes-row">'
                    f'<td colspan="5"><div class="opp-notes-text">{details_content}</div></td>'
                    f'</tr>'
                )
        opps_html = (
            f'<div class="section-card">'
            f'<h3>Opportunities</h3>'
            f'<div class="table-wrap">'
            f'<table class="opp-table">'
            f'<thead><tr><th>Name</th><th>Stage</th><th>Amount</th><th>Close Date</th><th>Owner</th></tr></thead>'
            f'<tbody>{rows}</tbody>'
            f'</table>'
            f'</div></div>'
        )

    # --- RECENT ACTIVITY / NEWS ---
    source_urls = c.source_urls
    all_news = (
        recent.fund_raisings
        + recent.major_announcements
        + recent.acquisitions
        + recent.partnerships
        + recent.executive_changes
    )
    all_news = _sort_news_reverse_chrono(all_news)
    news_html = ""
    if all_news:
        visible_limit = 5
        visible_items = "".join(
            f'<div class="news-item">{_linkify_sources(item, source_urls)}</div>'
            for item in all_news[:visible_limit]
        )
        hidden_items = ""
        toggle_btn = ""
        if len(all_news) > visible_limit:
            hidden_items = "".join(
                f'<div class="news-item news-hidden">{_linkify_sources(item, source_urls)}</div>'
                for item in all_news[visible_limit:]
            )
            extra = len(all_news) - visible_limit
            toggle_btn = (
                f'<button class="news-toggle-btn" onclick="toggleNewsExpand(this)">'
                f'Show {extra} more</button>'
            )
        news_html = (
            f'<div class="section-card news-card"><h3>Recent Activity &amp; Momentum</h3>'
            f'{visible_items}{hidden_items}{toggle_btn}</div>'
        )

    # --- INTELLIGENCE GRID (2-col: strategy+criteria, deals) ---
    strategy_html = ""
    strategy_parts = []
    if strategy.lending_types:
        strategy_parts.append(f'<div class="info-row"><div class="info-label">Lending Types</div><div class="info-value">{_render_tags(strategy.lending_types)}</div></div>')
    if strategy.facility_structures:
        strategy_parts.append(f'<div class="info-row"><div class="info-label">Structures</div><div class="info-value">{_render_tags(strategy.facility_structures)}</div></div>')
    if strategy.deal_types:
        strategy_parts.append(f'<div class="info-row"><div class="info-label">Deal Types</div><div class="info-value">{_render_tags(strategy.deal_types)}</div></div>')
    if strategy.sponsor_types:
        strategy_parts.append(f'<div class="info-row"><div class="info-label">Sponsor Focus</div><div class="info-value">{_render_tags(strategy.sponsor_types)}</div></div>')
    if strategy.syndication_approach:
        strategy_parts.append(f'<div class="info-row"><div class="info-label">Syndication</div><div class="info-value">{_render_tags(strategy.syndication_approach)}</div></div>')
    if strategy_parts:
        strategy_html = f'<div class="section-card"><h3>Investment Strategy</h3>{"".join(strategy_parts)}</div>'

    criteria_html = ""
    criteria_parts = []
    if criteria.check_sizes:
        criteria_parts.append(f'<div class="info-row"><div class="info-label">Check Sizes</div><div class="info-value">{", ".join(html.escape(s) for s in criteria.check_sizes)}</div></div>')
    if criteria.ebitda_thresholds:
        criteria_parts.append(f'<div class="info-row"><div class="info-label">EBITDA Thresholds</div><div class="info-value">{", ".join(html.escape(s) for s in criteria.ebitda_thresholds)}</div></div>')
    if strategy.industry_focus:
        criteria_parts.append(f'<div class="info-row"><div class="info-label">Industry Focus</div><div class="info-value">{_render_tags(strategy.industry_focus[:10])}</div></div>')
    if strategy.geographic_focus:
        criteria_parts.append(f'<div class="info-row"><div class="info-label">Geography</div><div class="info-value">{", ".join(html.escape(g) for g in strategy.geographic_focus)}</div></div>')
    if criteria_parts:
        criteria_html = f'<div class="section-card"><h3>Investment Criteria</h3>{"".join(criteria_parts)}</div>'

    deals_html = ""
    if portfolio.recent_deals:
        deal_items = "".join(f'<div class="deal-item">{_linkify_sources(d, source_urls)}</div>' for d in portfolio.recent_deals[:10])
        deals_html = f'<div class="section-card"><h3>Recent Transactions</h3>{deal_items}</div>'

    # --- COMPANY SUMMARY ---
    summary_html = ""
    summary_parts = []
    if summary.overview:
        summary_parts.append(f'<div class="summary-block"><h4>Overview</h4><p>{html.escape(summary.overview)}</p></div>')
    if summary.credit_focus:
        summary_parts.append(f'<div class="summary-block"><h4>Credit Focus</h4><p>{html.escape(summary.credit_focus)}</p></div>')
    if summary.notable_details:
        summary_parts.append(f'<div class="summary-block"><h4>Notable Details</h4><p>{html.escape(summary.notable_details)}</p></div>')
    if summary_parts:
        summary_html = f'<div class="section-card summary-section">{"".join(summary_parts)}</div>'

    # --- PEOPLE (accordion) ---
    people_html = ""
    if c.person_profiles:
        cards = "".join(
            _render_person_accordion(p, i == 0) for i, p in enumerate(c.person_profiles)
        )
        people_html = (
            f'<div class="people-section">'
            f'<div class="people-header">'
            f'<h3>People Intelligence</h3>'
            f'<button class="expand-all-btn" onclick="toggleAllAccordions(this)">Expand All</button>'
            f'</div>{cards}</div>'
        )

    # --- SOURCES FOOTER ---
    sources_footer_html = ""
    if source_urls:
        src_items = "".join(
            f'<div class="source-footer-item">'
            f'<span class="source-num">[{i+1}]</span> '
            f'<a href="{html.escape(url)}" target="_blank" class="source-footer-link" title="{html.escape(url)}">'
            f'{html.escape(_domain_from_url(url))}</a>'
            f'<span class="source-footer-path">{html.escape(url[url.find("/", 8):min(len(url), url.find("/", 8) + 40)] if url.find("/", 8) > 0 else "")}</span>'
            f'</div>'
            for i, url in enumerate(source_urls)
        )
        sources_footer_html = (
            f'<div class="section-card sources-footer">'
            f'<h3>Sources</h3>{src_items}</div>'
        )

    # --- ACCOUNT NOTES ---
    notes_html = ""
    if sf and sf.notes:
        note_parts = []
        for n in sf.notes:
            escaped = html.escape(n)
            if len(n) > 300:
                preview = html.escape(n[:250].rsplit(" ", 1)[0]) + "..."
                note_parts.append(
                    f'<div class="note-item note-expandable">'
                    f'<div class="note-preview">{preview} '
                    f'<button class="note-toggle" onclick="toggleNoteExpand(this)">Show full note</button></div>'
                    f'<div class="note-full" style="display:none">{escaped} '
                    f'<button class="note-toggle" onclick="toggleNoteExpand(this)">Show less</button></div>'
                    f'</div>'
                )
            else:
                note_parts.append(f'<div class="note-item">{escaped}</div>')
        note_items = "".join(note_parts)
        notes_html = f'<div class="section-card notes-section"><h3>Account Notes</h3>{note_items}</div>'

    return f"""
    <div id="detail-{idx}" class="detail-panel" style="display:none">
      <!-- HERO -->
      <div class="detail-hero">
        <div class="hero-main">
          <div class="hero-title-row"><h2>{name}</h2>{freshness_badge}</div>
          <div class="hero-stats">{meta_html}</div>
        </div>
        <div class="hero-fit">
          <div class="fit-circle {fit_class}">
            <div class="fit-num">{fit.total}</div>
            <div class="fit-lbl">Fit</div>
          </div>
        </div>
      </div>

      <!-- CRM ACCOUNT BAR -->
      {account_bar_html}

      <!-- DATA WARNING -->
      {data_warning_html}

      <!-- TALKING POINTS -->
      {talking_points_html}

      <!-- ACCOUNT NOTES -->
      {notes_html}

      <!-- OPPORTUNITIES -->
      {opps_html}

      <!-- RECENT ACTIVITY -->
      {news_html}

      <!-- INTELLIGENCE GRID -->
      <div class="intel-grid">
        {strategy_html}
        {criteria_html}
        {deals_html}
        {summary_html}
      </div>

      <!-- SOURCES -->
      {sources_footer_html}

      <!-- PEOPLE -->
      {people_html}
    </div>"""


def _render_person_accordion(person: PersonProfile, expanded: bool = False) -> str:
    """Render a person as an accordion with timeline."""
    name = html.escape(person.name)
    initials = "".join(w[0].upper() for w in person.name.split()[:2] if w)

    title = html.escape(person.current_title or "")
    title_display = title if title else "No title"

    # Email link
    email_link = ""
    if person.email:
        esc_email = html.escape(person.email)
        email_link = f'<a href="mailto:{esc_email}" class="person-email" onclick="event.stopPropagation()" title="{esc_email}">{esc_email}</a>'

    # LinkedIn button
    linkedin_btn = ""
    if person.linkedin_url:
        url = html.escape(person.linkedin_url)
        linkedin_btn = f'<a href="{url}" class="person-linkedin-btn" target="_blank" onclick="event.stopPropagation()">LinkedIn</a>'

    # CRM status badge
    crm_badge = ""
    if person.sf_status:
        crm_badge = f'<span class="person-crm-badge">{html.escape(person.sf_status)}</span>'

    # Last contacted
    last_contact = ""
    if person.last_contacted:
        last_contact = f'<span class="person-last-contact">Last: {html.escape(person.last_contacted)}</span>'

    open_class = "open" if expanded else ""

    # --- BODY ---
    body_parts = []

    # Bio summary callout
    bio = person.bio_summary or ""
    # Filter out LLM placeholder/fallback text that shouldn't be shown to users
    _bio_skip_phrases = [
        "no professional background",
        "no information found",
        "not found in the provided",
        "no details available",
        "does not contain any information",
        "could not find",
        "no data available",
    ]
    if bio and not any(p in bio.lower() for p in _bio_skip_phrases):
        body_parts.append(
            f'<div class="person-bio">{html.escape(bio)}</div>'
        )

    # Experience timeline
    exp_html = _render_experience_timeline(person)
    if exp_html:
        body_parts.append(exp_html)

    # Education
    if person.education:
        edu_items = ""
        for edu in person.education:
            degree = html.escape(edu.degree) if edu.degree else ""
            school = html.escape(edu.school)
            year = ""
            if edu.graduation_year:
                year = f'<span class="edu-year">{html.escape(edu.graduation_year)}</span>'
            edu_items += f'<div class="edu-item">{degree}{", " if degree else ""}{school} {year}</div>'
        body_parts.append(f'<div class="person-edu"><div class="section-label">Education</div>{edu_items}</div>')

    # CRM History
    if person.interactions:
        crm_items = ""
        for interaction in person.interactions[:10]:
            type_class = {
                "Call": "crm-call",
                "Email": "crm-email",
                "Meeting": "crm-meeting",
            }.get(interaction.activity_type, "crm-task")
            date_str = html.escape(interaction.date) if interaction.date else ""
            subject = html.escape(interaction.subject) if interaction.subject else ""
            notes_raw = interaction.notes or ""
            owner = html.escape(interaction.owner) if interaction.owner else ""

            crm_items += f'<div class="crm-item {type_class}">'
            crm_items += f'<div class="crm-header">'
            crm_items += f'<span class="crm-type">{html.escape(interaction.activity_type)}</span>'
            if date_str:
                crm_items += f'<span class="crm-date">{date_str}</span>'
            if owner:
                crm_items += f'<span class="crm-owner">{owner}</span>'
            crm_items += f'</div>'
            if subject:
                crm_items += f'<div class="crm-subject">{subject}</div>'
            if notes_raw:
                notes_escaped = html.escape(notes_raw)
                if len(notes_raw) < 150:
                    crm_items += f'<div class="crm-notes">{notes_escaped}</div>'
                else:
                    preview = html.escape(notes_raw[:120].rsplit(" ", 1)[0]) + "..."
                    crm_items += (
                        f'<div class="crm-notes-wrap">'
                        f'<div class="crm-notes-preview">{preview} '
                        f'<button class="crm-expand-btn" onclick="event.stopPropagation();toggleNotes(this)">Show more</button></div>'
                        f'<div class="crm-notes-full" style="display:none">{notes_escaped} '
                        f'<button class="crm-expand-btn" onclick="event.stopPropagation();toggleNotes(this)">Show less</button></div>'
                        f'</div>'
                    )
            crm_items += '</div>'

        body_parts.append(f'<div class="person-crm"><div class="section-label">CRM History</div>{crm_items}</div>')

    # Person sources
    if person.source_urls:
        src_links = "".join(
            f'<a href="{html.escape(u)}" class="person-source-link" target="_blank">{html.escape(_domain_from_url(u))}</a>'
            for u in person.source_urls[:6]
        )
        body_parts.append(f'<div class="person-sources"><div class="section-label">Sources</div><div class="person-source-list">{src_links}</div></div>')

    body_html = "".join(body_parts) if body_parts else '<div class="no-data">No additional data available</div>'

    return f"""
    <div class="accordion {open_class}">
      <div class="accordion-header" onclick="togglePerson(this)">
        <div class="accordion-left">
          <div class="avatar">{initials}</div>
          <div class="accordion-info">
            <div class="accordion-name">{name}</div>
            <div class="accordion-title">{title_display}</div>
            {email_link}
          </div>
        </div>
        <div class="accordion-right">
          {linkedin_btn}
          {crm_badge}
          {last_contact}
          <span class="accordion-arrow"></span>
        </div>
      </div>
      <div class="accordion-body">
        {body_html}
      </div>
    </div>"""


def _render_experience_timeline(person: PersonProfile) -> str:
    """Render work experience as a vertical timeline."""
    # Build timeline items: current role first, then prior
    items = []

    if person.current_title and person.current_company:
        duration = ""
        if person.tenure_current:
            duration = f' <span class="timeline-duration">({html.escape(person.tenure_current)})</span>'
        items.append({
            "title": html.escape(person.current_title),
            "firm": html.escape(person.current_company),
            "duration_raw": person.tenure_current or "",
            "duration_html": duration,
            "highlights": [],
            "is_current": True,
        })

    for exp in person.prior_experience:
        duration = ""
        if exp.duration:
            duration = f' <span class="timeline-duration">({html.escape(exp.duration)})</span>'
        items.append({
            "title": html.escape(exp.title) if exp.title else "Role",
            "firm": html.escape(exp.firm),
            "duration_raw": exp.duration or "",
            "duration_html": duration,
            "highlights": [html.escape(h) for h in exp.highlights],
            "is_current": False,
        })

    if not items:
        return ""

    timeline_items = ""
    for item in items:
        dot_class = "dot-current" if item["is_current"] else "dot-prior"
        highlights = ""
        if item["highlights"]:
            hl = "".join(f'<div class="timeline-highlight">{h}</div>' for h in item["highlights"])
            highlights = f'<div class="timeline-highlights">{hl}</div>'

        timeline_items += (
            f'<div class="timeline-item">'
            f'<div class="timeline-dot {dot_class}"></div>'
            f'<div class="timeline-content">'
            f'<div class="timeline-role">{item["title"]}</div>'
            f'<div class="timeline-firm">{item["firm"]}{item["duration_html"]}</div>'
            f'{highlights}'
            f'</div></div>'
        )

    return f'<div class="person-timeline"><div class="section-label">Experience</div><div class="timeline">{timeline_items}</div></div>'


def _render_tags(items: list[str], css_class: str = "tag") -> str:
    if not items:
        return ""
    return '<div class="tag-list">' + "".join(
        f'<span class="{css_class}">{html.escape(item)}</span>' for item in items
    ) + '</div>'


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Street Diligence Intelligence Dashboard</title>
  <style>
    :root {{
      /* SD Brand — green */
      --sd-primary: #28a745;
      --sd-dark: #218838;
      --sd-darker: #1e7e34;
      --sd-bg: #e7f5ec;
      --sd-border: #c3e6cb;
      --sd-light: #d4edda;

      /* Fit colors (semantic — unchanged) */
      --fit-high: #22c55e;
      --fit-medium: #eab308;
      --fit-low: #ef4444;

      /* CRM activity colors (semantic — unchanged) */
      --crm-call: #22c55e;
      --crm-email: #3b82f6;
      --crm-meeting: #8b5cf6;
      --crm-task: #f97316;

      /* Neutrals */
      --bg: #f8fafc;
      --card: #ffffff;
      --text: #1e293b;
      --text-secondary: #64748b;
      --border: #e2e8f0;
      --border-light: #f1f5f9;

      /* Layout */
      --sidebar-w: 280px;
      --header-h: 60px;
    }}

    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      height: 100vh;
      overflow: hidden;
    }}

    /* ---- HEADER ---- */
    .header {{
      height: var(--header-h);
      background: linear-gradient(135deg, var(--sd-darker) 0%, var(--sd-dark) 100%);
      color: white;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 24px;
      box-shadow: 0 2px 8px rgba(30,126,52,0.15);
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 100;
    }}
    .header h1 {{ font-size: 18px; font-weight: 700; letter-spacing: -0.3px; }}
    .header .subtitle {{ font-size: 12px; opacity: 0.8; }}
    .header-right {{ display: flex; align-items: center; gap: 12px; }}
    .print-btn {{
      padding: 6px 14px;
      background: rgba(255,255,255,0.15);
      border: 1px solid rgba(255,255,255,0.3);
      border-radius: 6px;
      color: white;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }}
    .print-btn:hover {{ background: rgba(255,255,255,0.25); }}

    /* ---- LAYOUT ---- */
    .layout {{
      display: flex;
      margin-top: var(--header-h);
      height: calc(100vh - var(--header-h));
    }}

    /* ---- SIDEBAR ---- */
    .sidebar {{
      width: var(--sidebar-w);
      min-width: var(--sidebar-w);
      background: var(--card);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }}
    .sidebar-controls {{
      padding: 12px;
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .sidebar-search {{
      width: 100%;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 13px;
      background: var(--bg);
    }}
    .sidebar-search:focus {{ outline: none; border-color: var(--sd-primary); }}
    .sidebar-filters {{
      display: flex;
      gap: 4px;
    }}
    .sidebar-filter-select {{
      flex: 1;
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 12px;
      background: var(--card);
    }}
    .sidebar-sort-btn {{
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--card);
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      color: var(--text-secondary);
    }}
    .sidebar-sort-btn:hover {{ border-color: var(--sd-primary); color: var(--sd-primary); }}
    .sidebar-sort-btn.active {{ background: var(--sd-primary); color: white; border-color: var(--sd-primary); }}

    .sidebar-list {{
      flex: 1;
      overflow-y: auto;
      padding: 4px 0;
    }}

    /* Fit group headers */
    .sidebar-group-header {{
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-secondary);
      padding: 10px 16px 4px;
    }}

    .sidebar-item {{
      padding: 10px 16px;
      cursor: pointer;
      border-left: 3px solid transparent;
      transition: all 0.15s;
    }}
    .sidebar-item:hover {{ background: var(--sd-bg); }}
    .sidebar-item.active {{
      background: var(--sd-bg);
      border-left-color: var(--sd-primary);
    }}
    .sidebar-item-top {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .sidebar-score {{
      min-width: 28px;
      height: 22px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      font-weight: 700;
      color: white;
    }}
    .sidebar-score.fit-high {{ background: var(--fit-high); }}
    .sidebar-score.fit-medium {{ background: var(--fit-medium); color: #422006; }}
    .sidebar-score.fit-low {{ background: var(--fit-low); }}
    .sidebar-name {{
      font-size: 13px;
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex: 1;
    }}
    .sidebar-item-bottom {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 2px;
      padding-left: 36px;
    }}
    .sidebar-people {{
      font-size: 11px;
      color: var(--text-secondary);
    }}
    .sidebar-crm-dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--sd-primary);
    }}
    .sidebar-opp {{
      font-size: 11px;
      font-weight: 700;
      color: var(--fit-high);
    }}
    .sidebar-warn {{
      width: 14px; height: 14px; border-radius: 50%;
      background: #ef4444; color: white; display: inline-flex;
      align-items: center; justify-content: center;
      font-size: 9px; font-weight: 800;
    }}

    /* ---- DETAIL PANEL ---- */
    .detail-area {{
      flex: 1;
      overflow-y: auto;
      background: var(--bg);
    }}
    .detail-panel {{ display: none; }}
    .detail-panel.active {{ display: block; }}

    .detail-hero {{
      background: linear-gradient(135deg, var(--sd-darker) 0%, var(--sd-primary) 100%);
      color: white;
      padding: 16px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 20px;
    }}
    .hero-title-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }}
    .hero-main h2 {{ font-size: 22px; font-weight: 700; }}
    .freshness-badge {{
      font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
      text-transform: uppercase; letter-spacing: 0.3px;
    }}
    .freshness-badge.fresh {{ background: rgba(34,197,94,0.25); color: #d1fae5; }}
    .freshness-badge.cached {{ background: rgba(255,255,255,0.15); color: rgba(255,255,255,0.7); }}
    .hero-stats {{ display: flex; gap: 16px; flex-wrap: wrap; }}
    .hero-stat {{
      display: flex;
      flex-direction: column;
      align-items: center;
      background: rgba(255,255,255,0.12);
      padding: 6px 12px;
      border-radius: 8px;
      min-width: 80px;
    }}
    .hero-stat-val {{ font-size: 14px; font-weight: 700; }}
    .hero-stat-lbl {{ font-size: 10px; opacity: 0.8; text-transform: uppercase; letter-spacing: 0.5px; }}

    .fit-circle {{
      width: 72px;
      height: 72px;
      border-radius: 50%;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      border: 3px solid rgba(255,255,255,0.5);
      flex-shrink: 0;
    }}
    .fit-circle.fit-high {{ background: rgba(34,197,94,0.3); }}
    .fit-circle.fit-medium {{ background: rgba(234,179,8,0.3); }}
    .fit-circle.fit-low {{ background: rgba(239,68,68,0.3); }}
    .fit-num {{ font-size: 24px; font-weight: 800; line-height: 1; }}
    .fit-lbl {{ font-size: 10px; text-transform: uppercase; opacity: 0.9; }}

    /* ---- ACCOUNT BAR ---- */
    .account-bar {{
      background: var(--sd-bg);
      border-bottom: 1px solid var(--sd-border);
      padding: 10px 24px;
    }}
    .acct-bar-title {{
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--sd-dark);
      margin-bottom: 6px;
    }}
    .acct-bar-items {{ display: flex; gap: 24px; flex-wrap: wrap; }}
    .acct-bar-item {{ display: flex; flex-direction: column; }}
    .acct-bar-label {{ font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.3px; }}
    .acct-bar-value {{ font-size: 14px; font-weight: 600; color: var(--text); }}

    /* ---- DATA WARNING ---- */
    .data-warning {{
      display: flex; align-items: flex-start; gap: 12px;
      background: #fef2f2; border: 1px solid #fecaca; border-left: 4px solid #ef4444;
      border-radius: 8px; padding: 12px 16px; margin: 12px 20px 0;
    }}
    .data-warning-icon {{
      width: 24px; height: 24px; border-radius: 50%;
      background: #ef4444; color: white; display: flex;
      align-items: center; justify-content: center;
      font-weight: 800; font-size: 14px; flex-shrink: 0;
    }}
    .data-warning-text {{ font-size: 13px; color: #991b1b; line-height: 1.5; }}
    .data-warning-text code {{
      background: #fee2e2; padding: 1px 6px; border-radius: 3px;
      font-size: 12px; font-family: monospace;
    }}

    /* ---- TALKING POINTS ---- */
    .talking-points {{
      background: #fefce8;
      border: 1px solid #fde68a;
      border-radius: 8px;
      padding: 12px 20px;
      margin: 12px 20px 0;
    }}
    .tp-header {{
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #92400e;
      margin-bottom: 8px;
    }}
    .talking-points ul {{
      list-style: disc;
      padding-left: 18px;
    }}
    .talking-points li {{
      font-size: 13px;
      color: #78350f;
      line-height: 1.6;
      margin-bottom: 4px;
    }}

    /* ---- SECTION CARDS ---- */
    .section-card {{
      background: var(--card);
      border-radius: 10px;
      padding: 14px 20px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.04);
      border: 1px solid var(--border);
    }}
    .section-card h3 {{
      font-size: 15px;
      font-weight: 700;
      color: var(--sd-dark);
      margin-bottom: 14px;
    }}

    /* ---- OPPORTUNITIES TABLE ---- */
    .table-wrap {{ overflow-x: auto; }}
    .opp-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .opp-table th {{
      text-align: left;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-secondary);
      padding: 8px 12px;
      border-bottom: 2px solid var(--border);
    }}
    .opp-table td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border-light);
      color: var(--text);
    }}
    .opp-table tr:hover td {{ background: var(--sd-bg); }}
    .opp-stage {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      background: var(--sd-light);
      color: var(--sd-darker);
    }}
    .opp-amount {{ font-weight: 600; white-space: nowrap; }}
    .opp-notes-row td {{
      padding: 0 12px 10px !important;
      border-bottom: 1px solid var(--border-light) !important;
      background: none !important;
    }}
    .opp-notes-text {{
      font-size: 12px;
      color: var(--text-secondary);
      line-height: 1.5;
      padding-left: 4px;
    }}
    .opp-notes-text strong {{
      font-weight: 600;
      color: var(--text);
    }}
    .opp-detail {{
      margin-bottom: 4px;
    }}
    .opp-detail:last-child {{
      margin-bottom: 0;
    }}
    .opp-roadblock {{
      color: #b45309;
    }}
    .opp-roadblock strong {{
      color: #92400e;
    }}
    .opp-note-item {{
      border-left: 2px solid var(--sd-primary);
      padding-left: 8px;
      margin-top: 4px;
    }}
    .opp-type-badge {{
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 3px;
      background: var(--bg);
      color: var(--text-secondary);
      font-weight: 500;
      margin-left: 4px;
    }}

    /* Space between detail sections */
    .detail-panel > .section-card,
    .detail-panel > .news-card,
    .detail-panel > .people-section,
    .detail-panel > .notes-section {{
      margin: 12px 20px;
    }}

    /* ---- NEWS ---- */
    .news-card {{ border-left: 4px solid var(--fit-medium); }}
    .news-card h3 {{ color: #92400e; }}
    .news-item {{
      padding: 6px 10px;
      background: #fffbeb;
      border-left: 3px solid #fbbf24;
      border-radius: 4px;
      margin-bottom: 4px;
      font-size: 12px;
      color: #78350f;
      line-height: 1.5;
    }}
    .news-hidden {{ display: none; }}
    .news-toggle-btn {{
      background: none;
      border: none;
      color: var(--sd-primary);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      padding: 4px 0;
    }}
    .news-toggle-btn:hover {{ text-decoration: underline; }}

    /* ---- INTEL GRID ---- */
    .intel-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 12px;
      padding: 12px 20px;
    }}

    /* ---- INFO ROWS / TAGS ---- */
    .info-row {{ padding: 10px 0; border-bottom: 1px solid var(--border-light); }}
    .info-row:last-child {{ border-bottom: none; }}
    .info-label {{ font-size: 10px; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
    .info-value {{ font-size: 13px; color: var(--text); }}
    .tag-list {{ display: flex; flex-wrap: wrap; gap: 5px; }}
    .tag {{
      background: var(--sd-bg);
      color: var(--sd-darker);
      padding: 3px 10px;
      border-radius: 5px;
      font-size: 12px;
      font-weight: 500;
      border: 1px solid var(--sd-border);
    }}

    /* ---- SUMMARY ---- */
    .summary-section .summary-block {{ margin-bottom: 12px; }}
    .summary-section .summary-block:last-child {{ margin-bottom: 0; }}
    .summary-block h4 {{ font-size: 12px; font-weight: 700; color: var(--sd-dark); text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 4px; }}
    .summary-block p {{ font-size: 13px; line-height: 1.7; color: var(--text); }}

    /* ---- DEAL ITEMS ---- */
    .deal-item {{
      padding: 8px 14px;
      background: var(--bg);
      border-left: 3px solid var(--sd-primary);
      border-radius: 4px;
      margin-bottom: 6px;
      font-size: 13px;
      color: var(--text);
    }}

    /* ---- SOURCE CITATIONS ---- */
    .source-link {{ color: var(--sd-primary); font-size: 11px; text-decoration: none; font-weight: 600; }}
    .source-link:hover {{ text-decoration: underline; }}

    /* ---- SOURCES FOOTER ---- */
    .sources-footer {{ margin: 12px 20px; }}
    .sources-footer h3 {{ font-size: 13px; }}
    .source-footer-item {{ font-size: 11px; color: var(--text-secondary); margin-bottom: 2px; }}
    .source-num {{ font-weight: 700; color: var(--sd-primary); }}
    .source-footer-link {{ color: var(--sd-primary); text-decoration: none; font-weight: 600; }}
    .source-footer-link:hover {{ text-decoration: underline; }}
    .source-footer-path {{ color: var(--text-secondary); font-size: 10px; margin-left: 2px; }}

    /* ---- PERSON SOURCES ---- */
    .person-sources {{ padding: 0 16px 8px; }}
    .person-source-list {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .person-source-link {{
      font-size: 11px; color: var(--sd-primary); text-decoration: none;
      background: var(--sd-bg); padding: 2px 8px; border-radius: 4px;
      border: 1px solid var(--sd-border);
    }}
    .person-source-link:hover {{ text-decoration: underline; background: var(--sd-light); }}

    /* ---- LINKEDIN BUTTON ---- */
    .person-linkedin-btn {{
      font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px;
      background: #0077b5; color: white; text-decoration: none;
    }}
    .person-linkedin-btn:hover {{ background: #005f8d; }}

    /* ---- PEOPLE SECTION ---- */
    .people-section {{ margin: 12px 20px; }}
    .people-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
    .people-header h3 {{ margin-bottom: 0; }}
    .people-section h3 {{
      font-size: 16px;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 12px;
    }}
    .expand-all-btn {{
      font-size: 11px; font-weight: 600; padding: 4px 12px; border-radius: 5px;
      border: 1px solid var(--border); background: var(--card); color: var(--text-secondary);
      cursor: pointer;
    }}
    .expand-all-btn:hover {{ border-color: var(--sd-primary); color: var(--sd-primary); }}

    /* ---- ACCORDION ---- */
    .accordion {{
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 8px;
      background: var(--card);
      overflow: hidden;
    }}
    .accordion-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 14px;
      cursor: pointer;
      user-select: none;
      transition: background 0.15s;
    }}
    .accordion-header:hover {{ background: var(--bg); }}
    .accordion-left {{ display: flex; align-items: center; gap: 12px; }}
    .avatar {{
      width: 36px;
      height: 36px;
      border-radius: 50%;
      background: var(--sd-primary);
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .accordion-name {{ font-size: 14px; font-weight: 600; color: var(--text); }}
    .accordion-title {{ font-size: 12px; color: var(--text-secondary); }}
    .person-email {{ font-size: 11px; color: var(--sd-primary); text-decoration: none; }}
    .person-email:hover {{ text-decoration: underline; }}
    .accordion-right {{ display: flex; align-items: center; gap: 10px; }}
    .person-crm-badge {{
      font-size: 10px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
      background: var(--sd-bg);
      color: var(--sd-dark);
    }}
    .person-last-contact {{
      font-size: 11px;
      color: var(--text-secondary);
    }}
    .accordion-arrow {{
      width: 8px;
      height: 8px;
      border-right: 2px solid var(--text-secondary);
      border-bottom: 2px solid var(--text-secondary);
      transform: rotate(45deg);
      transition: transform 0.2s;
    }}
    .accordion.open .accordion-arrow {{ transform: rotate(-135deg); }}

    .accordion-body {{
      max-height: 0;
      overflow: hidden;
      transition: max-height 0.3s ease;
    }}
    .accordion.open .accordion-body {{
      max-height: 3000px;
    }}
    .accordion-body > * {{
      padding: 0 16px;
    }}
    .accordion-body > *:last-child {{
      padding-bottom: 16px;
    }}

    .section-label {{
      font-size: 10px;
      font-weight: 700;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 8px;
      margin-top: 14px;
    }}

    .person-bio {{
      font-size: 13px;
      color: var(--text);
      line-height: 1.6;
      padding: 12px 16px;
      background: var(--sd-bg);
      border-radius: 6px;
      border-left: 3px solid var(--sd-primary);
      margin-top: 12px;
    }}

    /* ---- TIMELINE ---- */
    .timeline {{ position: relative; padding-left: 20px; }}
    .timeline::before {{
      content: '';
      position: absolute;
      left: 6px;
      top: 4px;
      bottom: 4px;
      width: 2px;
      background: var(--border);
    }}
    .timeline-item {{
      position: relative;
      padding-bottom: 14px;
      display: flex;
      gap: 12px;
    }}
    .timeline-item:last-child {{ padding-bottom: 0; }}
    .timeline-dot {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      position: absolute;
      left: -19px;
      top: 4px;
      border: 2px solid var(--card);
      z-index: 1;
    }}
    .dot-current {{ background: var(--sd-primary); }}
    .dot-prior {{ background: #94a3b8; }}
    .timeline-content {{ flex: 1; min-width: 0; }}
    .timeline-role {{ font-size: 13px; font-weight: 600; color: var(--text); }}
    .timeline-firm {{ font-size: 12px; color: var(--text-secondary); }}
    .timeline-duration {{ font-weight: 600; color: var(--sd-dark); }}
    .timeline-highlights {{ padding-left: 8px; margin-top: 4px; }}
    .timeline-highlight {{ font-size: 12px; color: var(--text-secondary); line-height: 1.5; }}
    .timeline-highlight::before {{ content: '- '; }}

    /* ---- EDUCATION ---- */
    .edu-item {{
      font-size: 13px;
      color: var(--text);
      margin-bottom: 4px;
      padding-left: 12px;
      border-left: 2px solid var(--border);
    }}
    .edu-year {{
      display: inline-block;
      background: var(--sd-bg);
      color: var(--sd-dark);
      padding: 1px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }}

    /* ---- CRM HISTORY (inside accordion) ---- */
    .person-crm {{ padding: 0 16px 16px; }}
    .crm-item {{
      padding: 8px 12px;
      border-radius: 6px;
      margin-bottom: 6px;
      font-size: 12px;
      border-left: 3px solid var(--border);
      background: var(--bg);
    }}
    .crm-call {{ border-left-color: var(--crm-call); background: #f0fdf4; }}
    .crm-email {{ border-left-color: var(--crm-email); background: var(--sd-bg); }}
    .crm-meeting {{ border-left-color: var(--crm-meeting); background: #faf5ff; }}
    .crm-task {{ border-left-color: var(--crm-task); background: #fff7ed; }}
    .crm-header {{ display: flex; gap: 10px; align-items: center; margin-bottom: 2px; }}
    .crm-type {{ font-weight: 700; font-size: 10px; text-transform: uppercase; }}
    .crm-date {{ color: var(--text-secondary); font-size: 11px; }}
    .crm-owner {{ color: var(--text-secondary); font-size: 11px; font-style: italic; }}
    .crm-subject {{ font-weight: 600; color: var(--text); margin-bottom: 2px; }}
    .crm-notes {{ color: var(--text-secondary); line-height: 1.5; white-space: pre-wrap; }}
    .crm-notes-wrap {{ margin-top: 2px; }}
    .crm-notes-preview {{ color: var(--text-secondary); line-height: 1.5; }}
    .crm-notes-full {{ color: var(--text-secondary); line-height: 1.5; white-space: pre-wrap; }}
    .crm-expand-btn {{
      background: none;
      border: none;
      color: var(--sd-primary);
      font-size: 11px;
      cursor: pointer;
      padding: 0;
      font-weight: 600;
    }}
    .crm-expand-btn:hover {{ text-decoration: underline; }}

    /* ---- NOTES SECTION ---- */
    .notes-section {{ margin: 12px 20px; }}
    .notes-section h3 {{
      font-size: 15px;
      font-weight: 700;
      color: var(--sd-dark);
      margin-bottom: 12px;
    }}
    .note-item {{
      padding: 10px 14px;
      background: var(--bg);
      border-left: 3px solid var(--sd-primary);
      border-radius: 4px;
      margin-bottom: 6px;
      font-size: 13px;
      color: var(--text);
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .note-toggle {{
      background: none; border: none; color: var(--sd-primary);
      font-size: 11px; font-weight: 600; cursor: pointer; padding: 0; margin-left: 4px;
    }}
    .note-toggle:hover {{ text-decoration: underline; }}

    .no-data {{
      padding: 20px 16px;
      color: var(--text-secondary);
      font-size: 13px;
      font-style: italic;
    }}

    /* ---- SUMMARY CARDS (stats) ---- */
    .summary-cards {{
      display: flex;
      gap: 8px;
      padding: 0 24px;
      flex-wrap: wrap;
    }}
    .stat-card {{
      background: var(--card);
      padding: 10px 16px;
      border-radius: 8px;
      border: 1px solid var(--border);
      text-align: center;
      min-width: 80px;
    }}
    .stat-value {{ font-size: 20px; font-weight: 700; color: var(--sd-primary); }}
    .stat-label {{ font-size: 10px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.4px; }}
    .stat-high .stat-value {{ color: var(--fit-high); }}
    .stat-medium .stat-value {{ color: var(--fit-medium); }}
    .stat-low .stat-value {{ color: var(--fit-low); }}
    .stat-intel .stat-value {{ color: var(--sd-primary); }}

    /* ---- EMPTY STATE ---- */
    .empty-state {{
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--text-secondary);
      font-size: 15px;
    }}

    /* ---- PRINT ---- */
    @media print {{
      body {{ overflow: visible; height: auto; }}
      .header {{ position: static; }}
      .sidebar {{ display: none !important; }}
      .layout {{ display: block; margin-top: 0; height: auto; }}
      .detail-area {{ overflow: visible; }}
      .detail-panel {{ display: block !important; page-break-before: always; }}
      .detail-hero {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .accordion-body {{ max-height: none !important; }}
      .accordion {{ break-inside: avoid; }}
    }}

    /* ---- MOBILE ---- */
    @media (max-width: 768px) {{
      .layout {{ flex-direction: column; }}
      .sidebar {{
        width: 100%;
        min-width: 100%;
        max-height: 160px;
        border-right: none;
        border-bottom: 1px solid var(--border);
      }}
      .sidebar-list {{
        display: flex;
        overflow-x: auto;
        overflow-y: hidden;
        padding: 4px;
        gap: 4px;
      }}
      .sidebar-item {{
        min-width: 160px;
        border-left: none;
        border-bottom: 3px solid transparent;
        flex-shrink: 0;
      }}
      .sidebar-item.active {{ border-bottom-color: var(--sd-primary); border-left-color: transparent; }}
      .sidebar-item-bottom {{ padding-left: 0; }}
      .detail-hero {{ padding: 16px; }}
      .hero-main h2 {{ font-size: 20px; }}
      .intel-grid {{ grid-template-columns: 1fr; padding: 12px; }}
      .talking-points, .section-card, .people-section, .notes-section {{ margin: 12px !important; }}
    }}
  </style>
</head>
<body>
  <!-- HEADER -->
  <div class="header">
    <div>
      <h1>Street Diligence Intelligence Dashboard</h1>
      <div class="subtitle">Generated: {generated_at}</div>
    </div>
    <div class="header-right">
      {summary_stats}
      <button class="print-btn" onclick="window.print()">Print</button>
    </div>
  </div>

  <div class="layout">
    <!-- SIDEBAR -->
    <div class="sidebar">
      <div class="sidebar-controls">
        <input type="text" id="sidebar-search" class="sidebar-search"
               placeholder="Search companies..." oninput="filterCompanies()">
        <div class="sidebar-filters">
          <select id="fit-filter" class="sidebar-filter-select" onchange="filterCompanies()">
            <option value="">All Fits</option>
            <option value="High">High</option>
            <option value="Medium">Medium</option>
            <option value="Low">Low</option>
          </select>
          <button class="sidebar-sort-btn active" data-sort="fit" onclick="sortCompanies('fit', this)">Fit</button>
          <button class="sidebar-sort-btn" data-sort="name" onclick="sortCompanies('name', this)">A-Z</button>
          <button class="sidebar-sort-btn" data-sort="people" onclick="sortCompanies('people', this)">#</button>
        </div>
      </div>
      <div class="sidebar-list" id="sidebar-list">
        {sidebar_items}
      </div>
    </div>

    <!-- DETAIL PANEL -->
    <div class="detail-area" id="detail-area">
      <div class="empty-state" id="empty-state">Select a company from the sidebar</div>
      {detail_panels}
    </div>
  </div>

  <script>
    const companiesData = {companies_json};
    let currentIdx = -1;
    let sortedOrder = [];

    function selectCompany(idx) {{
      // Deselect previous
      document.querySelectorAll('.sidebar-item').forEach(s => s.classList.remove('active'));
      document.querySelectorAll('.detail-panel').forEach(d => {{
        d.style.display = 'none';
        d.classList.remove('active');
      }});

      // Select new
      const sidebar = document.getElementById('sidebar-' + idx);
      const detail = document.getElementById('detail-' + idx);
      const empty = document.getElementById('empty-state');

      if (sidebar) sidebar.classList.add('active');
      if (detail) {{
        detail.style.display = 'block';
        detail.classList.add('active');
      }}
      if (empty) empty.style.display = detail ? 'none' : 'flex';

      currentIdx = idx;

      // Scroll detail area to top
      document.getElementById('detail-area').scrollTop = 0;
    }}

    function filterCompanies() {{
      const search = document.getElementById('sidebar-search').value.toLowerCase();
      const fitFilter = document.getElementById('fit-filter').value;
      let firstVisible = -1;

      companiesData.forEach(c => {{
        const el = document.getElementById('sidebar-' + c.idx);
        if (!el) return;
        const nameMatch = !search || c.name.toLowerCase().includes(search) || (c.industries || '').toLowerCase().includes(search);
        const fitMatch = !fitFilter || c.fitRating === fitFilter;
        const visible = nameMatch && fitMatch;
        el.style.display = visible ? '' : 'none';
        if (visible && firstVisible === -1) firstVisible = c.idx;
      }});

      // If current company is hidden, switch to first visible
      const currentEl = document.getElementById('sidebar-' + currentIdx);
      if (!currentEl || currentEl.style.display === 'none') {{
        if (firstVisible >= 0) selectCompany(firstVisible);
      }}
    }}

    function sortCompanies(sortBy, btn) {{
      // Update button states
      document.querySelectorAll('.sidebar-sort-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');

      const sorted = [...companiesData];
      if (sortBy === 'fit') sorted.sort((a, b) => b.fitScore - a.fitScore);
      else if (sortBy === 'name') sorted.sort((a, b) => a.name.localeCompare(b.name));
      else if (sortBy === 'people') sorted.sort((a, b) => b.peopleCount - a.peopleCount);

      // Reorder sidebar items
      const list = document.getElementById('sidebar-list');
      sorted.forEach(c => {{
        const el = document.getElementById('sidebar-' + c.idx);
        if (el) list.appendChild(el);
      }});

      sortedOrder = sorted.map(c => c.idx);

      // Select first if nothing selected
      if (currentIdx === -1 && sorted.length > 0) {{
        selectCompany(sorted[0].idx);
      }}
    }}

    function togglePerson(headerEl) {{
      const accordion = headerEl.closest('.accordion');
      accordion.classList.toggle('open');
    }}

    function toggleAllAccordions(btn) {{
      const section = btn.closest('.people-section');
      const accordions = section.querySelectorAll('.accordion');
      const allOpen = Array.from(accordions).every(a => a.classList.contains('open'));
      accordions.forEach(a => {{
        if (allOpen) a.classList.remove('open');
        else a.classList.add('open');
      }});
      btn.textContent = allOpen ? 'Expand All' : 'Collapse All';
    }}

    function toggleNewsExpand(btn) {{
      const card = btn.closest('.news-card');
      const hidden = card.querySelectorAll('.news-hidden');
      const isExpanded = btn.dataset.expanded === 'true';
      hidden.forEach(el => el.style.display = isExpanded ? 'none' : '');
      if (isExpanded) {{
        btn.textContent = 'Show ' + hidden.length + ' more';
        btn.dataset.expanded = 'false';
      }} else {{
        btn.textContent = 'Show less';
        btn.dataset.expanded = 'true';
      }}
    }}

    function toggleNotes(btn) {{
      const wrap = btn.closest('.crm-notes-wrap');
      const preview = wrap.querySelector('.crm-notes-preview');
      const full = wrap.querySelector('.crm-notes-full');
      if (preview.style.display === 'none') {{
        preview.style.display = '';
        full.style.display = 'none';
      }} else {{
        preview.style.display = 'none';
        full.style.display = '';
      }}
    }}

    function toggleNoteExpand(btn) {{
      const item = btn.closest('.note-expandable');
      const preview = item.querySelector('.note-preview');
      const full = item.querySelector('.note-full');
      if (preview.style.display === 'none') {{
        preview.style.display = '';
        full.style.display = 'none';
      }} else {{
        preview.style.display = 'none';
        full.style.display = '';
      }}
    }}

    // Keyboard navigation
    document.addEventListener('keydown', function(e) {{
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

      // Get visible sidebar items in order
      const visible = [];
      document.querySelectorAll('.sidebar-item').forEach(el => {{
        if (el.style.display !== 'none') {{
          visible.push(parseInt(el.dataset.idx));
        }}
      }});
      if (visible.length === 0) return;

      const curPos = visible.indexOf(currentIdx);

      if (e.key === 'ArrowDown' || e.key === 'j') {{
        e.preventDefault();
        const next = curPos < visible.length - 1 ? curPos + 1 : 0;
        selectCompany(visible[next]);
        document.getElementById('sidebar-' + visible[next]).scrollIntoView({{ block: 'nearest' }});
      }} else if (e.key === 'ArrowUp' || e.key === 'k') {{
        e.preventDefault();
        const prev = curPos > 0 ? curPos - 1 : visible.length - 1;
        selectCompany(visible[prev]);
        document.getElementById('sidebar-' + visible[prev]).scrollIntoView({{ block: 'nearest' }});
      }}
    }});

    // Initialize
    window.onload = function() {{
      sortCompanies('fit', document.querySelector('.sidebar-sort-btn[data-sort="fit"]'));
      if (companiesData.length > 0) {{
        // Select highest fit company
        const sorted = [...companiesData].sort((a, b) => b.fitScore - a.fitScore);
        selectCompany(sorted[0].idx);
      }}
    }};
  </script>
</body>
</html>"""
