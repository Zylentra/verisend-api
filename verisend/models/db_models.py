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


# TODO: For the PoC, user_id is pulled from the auth token. The User model
# exists for FK integrity and to support the full user profile flow later.
class User(SQLModel, table=True):
    __tablename__ = "users"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    first_name: str
    last_name: str

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    forms: list["Form"] = Relationship(back_populates="user")
    submissions: list["FormSubmission"] = Relationship(back_populates="user")
    standard_field_values: list["UserStandardFieldValue"] = Relationship(back_populates="user")


class Form(SQLModel, table=True):
    __tablename__ = "forms"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID | None = Field(default=None, foreign_key="users.id", index=True)

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

    user: User = Relationship(back_populates="forms")
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
    user_id: UUID = Field(foreign_key="users.id", index=True)

    data: list = Field(sa_column=Column(JSON, nullable=False))

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    form: Form = Relationship(back_populates="submissions")
    user: User = Relationship(back_populates="submissions")


# TODO: Add FormAssignment table when multi-user flows are needed.
# For now any user can fill in any active form.


class UserStandardFieldValue(SQLModel, table=True):
    __tablename__ = "user_standard_field_values"  # type: ignore
    __table_args__ = (UniqueConstraint("user_id", "standard_field_key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    standard_field_key: str = Field(index=True)
    value: str

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    user: User = Relationship(back_populates="standard_field_values")
