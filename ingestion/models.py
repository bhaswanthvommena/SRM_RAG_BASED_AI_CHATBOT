from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# Post types we index in MVP (REST rest_base names)
MVP_REST_TYPES: list[str] = [
    "pages",
    "programme",
    "faculty-profile",
    "staff",
    "leadership",
    "news",
    "press-releases",
    "placement-statistic",
    "student-placement",
]

# Sitemap filenames / URL patterns that are taxonomy noise
TAXONOMY_SITEMAP_HINTS: tuple[str, ...] = (
    "interest-sitemap",
    "category-sitemap",
    "news-type-sitemap",
    "language-category-sitemap",
    "leadership-category-sitemap",
    "newsletter-year-sitemap",
    "press-release-type-sitemap",
    "school-category-sitemap",
    "testimonial-type-sitemap",
    "videos-page-sitemap",
    "events_cat-sitemap",
    "acadmic-group-sitemap",
    "department-research-area-sitemap",
    "document-type-sitemap",
)

JUNK_URL_SUBSTRINGS: tuple[str, ...] = (
    "/elementor-",
    "export-pages-from-live-site",
    "international-admissions-demo",
    "/wp-json/",
    "/feed/",
    "/page/",
    "?s=",
    "/tag/",
    "/interest/",
)

# Map REST rest_base -> doc_type label stored in DB
REST_TO_DOC_TYPE: dict[str, str] = {
    "pages": "page",
    "programme": "programme",
    "faculty-profile": "faculty",
    "staff": "staff",
    "leadership": "leadership",
    "news": "news",
    "press-releases": "press_release",
    "placement-statistic": "placement",
    "student-placement": "placement",
}


class ManifestItem(BaseModel):
    url: str
    wp_type: str
    wp_id: int | None = None
    title: str | None = None
    modified: datetime | None = None
    published: datetime | None = None
    school: str | None = None
    department: str | None = None
    doc_type: str = "page"
    is_stale: bool = False
    source: str = "rest"  # rest | sitemap
    status: str = "pending"  # pending | fetched | parsed | skipped | error
    extra: dict[str, Any] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    url: str
    title: str | None = None
    text: str
    doc_type: str = "page"
    wp_type: str | None = None
    wp_id: int | None = None
    school: str | None = None
    department: str | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    is_stale: bool = False
    source: str = "html"
    content_hash: str
    pdf_links: list[str] = Field(default_factory=list)
    structured: dict[str, Any] = Field(default_factory=dict)
    sections: list[dict[str, Any]] = Field(default_factory=list)


class Chunk(BaseModel):
    ord: int
    heading_path: str | None = None
    text: str
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
