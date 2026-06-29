"""Shared HTML-stripping helper for data sources.

Used by IR-page and EDGAR-KPI extraction to flatten HTML to prose/cell text.
Single canonical implementation so a behavior fix applies once, not per source.
"""
from __future__ import annotations

import re


def strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace — returns plain text."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    return re.sub(r"\s+", " ", text).strip()
