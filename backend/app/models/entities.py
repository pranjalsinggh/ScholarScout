"""Relational schema - papers, structure, people, citations, events, watchlist.

Even without authentication, a `owner` column keeps the architecture extensible
for future user accounts.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import relationship

from app.database.database import Base
from app.database.types import EncryptedJSON, EncryptedText


def _uid() -> str:
    return uuid.uuid4().hex[:12]


class Paper(Base):
    __tablename__ = "papers"

    id = Column(String, primary_key=True, default=_uid)
    owner = Column(String, index=True, default="local")  # extensible to real user accounts
    filename = Column(EncryptedText)
    file_path = Column(String)
    size_bytes = Column(Integer, default=0)
    file_key_w1 = Column(Text)   # file key wrapped with the owner's browser key
    file_key_w2 = Column(Text)   # file key wrapped with the server key (restart recovery)

    title = Column(EncryptedText, default="")
    authors = Column(EncryptedJSON, default=list)           # [{name, affiliation?}]
    abstract = Column(EncryptedText, default="")
    year = Column(Integer)
    venue = Column(EncryptedText)
    doi = Column(String)
    arxiv_id = Column(String)
    keywords = Column(EncryptedJSON, default=list)
    page_count = Column(Integer, default=0)
    funding = Column(EncryptedJSON, default=list)            # [{funder, grant?}]
    openalex_id = Column(String, index=True)

    status = Column(String, default="queued")      # queued|processing|ready|failed
    dedup_key = Column(String, index=True)         # arxiv:ID | doi:DOI | title:SHA256
    stages = Column(EncryptedJSON, default=list)            # [{key,label,state,detail}]
    error = Column(EncryptedText)
    ecosystem_ok = Column(Boolean, default=True)   # external APIs succeeded
    ecosystem_note = Column(EncryptedText)

    meta = Column(EncryptedJSON, default=dict)              # methods, datasets, figures, tables...
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    sections = relationship("PaperSection", back_populates="paper",
                            cascade="all, delete-orphan", order_by="PaperSection.position")
    chunks = relationship("Chunk", back_populates="paper",
                          cascade="all, delete-orphan")
    citations = relationship("Citation", back_populates="paper",
                             cascade="all, delete-orphan")
    events = relationship("ResearchEvent", back_populates="paper",
                          cascade="all, delete-orphan")
    authors_rel = relationship("Author", back_populates="paper",
                               cascade="all, delete-orphan")


class PaperSection(Base):
    __tablename__ = "paper_sections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    name = Column(String)          # Abstract / Introduction / Methods ...
    position = Column(Integer, default=0)
    page_start = Column(Integer)
    text = Column(EncryptedText)

    paper = relationship("Paper", back_populates="sections")


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    section_id = Column(Integer, ForeignKey("paper_sections.id", ondelete="SET NULL"))
    section_name = Column(EncryptedText)
    page = Column(Integer)
    text = Column(EncryptedText)
    vector_id = Column(Integer)    # position in the vector store

    paper = relationship("Paper", back_populates="chunks")


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (UniqueConstraint("paper_id", "name", name="uq_author_per_paper"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    name = Column(EncryptedText, index=True)
    position = Column(Integer, default=0)
    affiliation = Column(EncryptedText)
    openalex_id = Column(String, index=True)
    role = Column(String, default="original")      # original|extender|challenger|major
    h_index = Column(Integer)
    works_count = Column(Integer)
    research_areas = Column(EncryptedJSON, default=list)
    last_seen = Column(String)                     # ISO date of last OpenAlex view

    paper = relationship("Paper", back_populates="authors_rel")


PaperAuthor = Author  # readable alias for the join row (single-paper scope)


class Citation(Base):
    """A work connected to the paper (referenced, citing, or related)."""
    __tablename__ = "citations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    direction = Column(String)     # referenced|citing|related
    title = Column(EncryptedText)
    authors = Column(EncryptedJSON, default=list)
    year = Column(Integer)
    venue = Column(EncryptedText)
    doi = Column(String)
    openalex_id = Column(String)
    arxiv_id = Column(String)
    cited_by_count = Column(Integer, default=0)
    is_replication = Column(Boolean, default=False)
    is_contradictory = Column(Boolean, default=False)
    is_correction = Column(Boolean, default=False)
    is_extension = Column(Boolean, default=False)
    institution_hints = Column(EncryptedJSON, default=list)
    abstract_inverted = Column(EncryptedJSON)   # OpenAlex raw abstract index (for snippets)
    snippet = Column(EncryptedText)             # reason / matched keywords
    score = Column(Float, default=0.0) # relatedness for `related` rows

    paper = relationship("Paper", back_populates="citations")


class ResearchEvent(Base):
    """Timeline entry: milestones, follow-ups, replications, corrections, news..."""
    __tablename__ = "research_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    kind = Column(String)          # publication|followup|replication|contradiction|
                                   # correction|retraction|newdataset|newmethod|news|milestone
    date_year = Column(Integer)
    date_text = Column(String)
    title = Column(EncryptedText)
    description = Column(EncryptedText)
    source_type = Column(String)   # academic|preprint|news|commentary|database
    source_name = Column(String)
    source_url = Column(String)
    citation_id = Column(Integer, ForeignKey("citations.id", ondelete="SET NULL"))
    confidence = Column(String, default="high")  # high|medium|heuristic

    paper = relationship("Paper", back_populates="events")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(String, index=True, default="local")
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    role = Column(String)          # user|assistant
    content = Column(Text)
    evidence = Column(JSON, default=list)
    mode = Column(String, default="extractive")   # extractive|synthesized
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class WatchItem(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(String, index=True, default="local")
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    label = Column(EncryptedText)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    last_checked = Column(DateTime)
    last_count = Column(Integer, default=0)
    updates = Column(EncryptedJSON, default=list)  # [{kind, title, url, date}]


class RetrievalProfile(Base):
    """Feedback-learned retrieval tuning, per paper.

    Grows from thumbs-up/down on chat answers: sections that produced helpful
    evidence gain weight, terms from well-received questions gain weight, and
    everything slowly decays so recent feedback dominates. Fully inspectable
    via GET /papers/{id}/retrieval-profile.
    """
    __tablename__ = "retrieval_profiles"

    owner = Column(String, index=True, default="local")
    paper_id = Column(String, ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    sections = Column(EncryptedJSON, default=dict)   # {section_name: weight -0.5..0.5}
    terms = Column(EncryptedJSON, default=dict)      # {term: weight}
    up_count = Column(Integer, default=0)
    down_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)


class ApiCache(Base):
    """Time-boxed encrypted owner-scoped answer cache."""
    __tablename__ = "api_cache"

    key = Column(String, primary_key=True)
    payload = Column(EncryptedJSON)
    expires_at = Column(DateTime, index=True)


class ProviderUsage(Base):
    """Private, content-free token estimates for one anonymous owner."""
    __tablename__ = "provider_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(String, index=True, nullable=False)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    input_tokens_estimate = Column(Integer, default=0)
    output_tokens_estimate = Column(Integer, default=0)
    cost_usd_estimate = Column(Float)
    created_at = Column(DateTime, default=dt.datetime.utcnow)


class OwnerSession(Base):
    """Short-lived anonymous session used to expire abandoned research."""
    __tablename__ = "owner_sessions"

    owner = Column(String, primary_key=True)
    last_seen = Column(DateTime, default=dt.datetime.utcnow, index=True)
    cloud_ai_opt_in = Column(Boolean, default=False)
    research_lookup_opt_in = Column(Boolean, default=False)


class EndedSession(Base):
    """Content-free revocation: an ended browser key must never become live again.

    Keep only the irreversible owner hash and revocation time. There is no
    browser token, document, question or identity in this record.
    """
    __tablename__ = "ended_sessions"

    owner = Column(String, primary_key=True)
    ended_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
