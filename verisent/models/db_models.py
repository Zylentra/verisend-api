from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4
from enum import Enum
from sqlmodel import Column, DateTime, Field, JSON, Relationship, SQLModel, UniqueConstraint


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class User(SQLModel, table=True):
    __tablename__ = "users"  # type: ignore

    id: str = Field(primary_key=True)  # Clerk user ID (e.g. "user_2abc123")
    email: str = Field(unique=True, index=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    memberships: list["OrgMembership"] = Relationship(back_populates="user")


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    business_name: str | None = None
    registration_number: str | None = None
    address: str
    owner_id: str = Field(foreign_key="users.id", index=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    owner: User = Relationship()
    members: list["OrgMembership"] = Relationship(back_populates="organization")
    api_keys: list["OrgApiKey"] = Relationship(back_populates="organization")


class OrgMembership(SQLModel, table=True):
    __tablename__ = "org_memberships"  # type: ignore
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="organizations.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    organization: Organization = Relationship(back_populates="members")
    user: User = Relationship(back_populates="memberships")


class OrgApiKey(SQLModel, table=True):
    __tablename__ = "org_api_keys"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="organizations.id", index=True)
    name: str
    key_hash: str = Field(unique=True, index=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    organization: Organization = Relationship(back_populates="api_keys")


class Form(SQLModel, table=True):
    __tablename__ = "forms"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="organizations.id", index=True)

    name: str
    original_filename: str
    pdf_url: str

    summary: str | None = None
    context: str | None = None
    styling: dict | None = Field(default=None, sa_column=Column(JSON))

    is_active: bool = Field(default=False, index=True)
    is_deleted: bool = Field(default=False, index=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    images: list["FormImage"] = Relationship(back_populates="form")
    sections: list["FormSection"] = Relationship(back_populates="form")
    job: Optional["ProcessingJob"] = Relationship(back_populates="form")
    submissions: list["FormSubmission"] = Relationship(back_populates="form")


class ProcessingJob(SQLModel, table=True):
    __tablename__ = "processing_jobs"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    form_id: UUID = Field(foreign_key="forms.id", index=True)

    status: str = Field(default=JobStatus.PENDING.value, index=True)
    progress: int = Field(default=0)
    current_step: str | None = None
    error: str | None = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    form: Form = Relationship(back_populates="job")


class FormImage(SQLModel, table=True):
    __tablename__ = "form_images"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    form_id: UUID = Field(foreign_key="forms.id", index=True)

    page_number: int
    image_url: str

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    form: Form = Relationship(back_populates="images")


class FormSection(SQLModel, table=True):
    __tablename__ = "form_sections"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    form_id: UUID = Field(foreign_key="forms.id", index=True)

    section_number: int = Field(index=True)
    name: str
    description: str | None = None
    page_start: int
    page_end: int

    fields: list | None = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    form: Form = Relationship(back_populates="sections")


class FormSubmission(SQLModel, table=True):
    __tablename__ = "form_submissions"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    form_id: UUID = Field(foreign_key="forms.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)

    data_url: str | None = None
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    form: Form = Relationship(back_populates="submissions")
    user: User = Relationship()


class StandardField(SQLModel, table=True):
    __tablename__ = "standard_fields"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(unique=True, index=True)
    label: str
    field_type: str
    group: str | None = None
    default_options: list | None = Field(default=None, sa_column=Column(JSON))
    description: str | None = None
