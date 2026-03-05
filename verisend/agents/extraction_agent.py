import json
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.providers.google import GoogleProvider

from pydantic_ai.messages import ImageUrl, UserContent

from verisend.settings import settings


# =============================================================================
# Standard fields context — loaded once at startup
# =============================================================================

def _load_standard_fields() -> str:
    path = Path(__file__).parent.parent / "data" / "standard_fields.json"
    fields = json.loads(path.read_text())
    lines = []
    for f in fields:
        line = f"- {f['key']} ({f['field_type']}): {f['description']}"
        if f.get("default_options"):
            line += f" | default options: {', '.join(f['default_options'])}"
        lines.append(line)
    return "\n".join(lines)


STANDARD_FIELDS_CONTEXT = _load_standard_fields()


# =============================================================================
# Output schema
# =============================================================================

from pydantic import BaseModel, Field

class ExtractedField(BaseModel):
    label: str = Field(description="Clean human-readable label for the field e.g. 'Date of Birth'")
    field_type: str = Field(description="One of: short_text, long_text, email, phone, date, number, dropdown, radio, checkbox, file_upload, signature")
    required: bool = Field(default=False, description="True if the field is marked as required, mandatory, or has an asterisk")
    placeholder: str | None = Field(default=None, description="Example or hint text shown inside the field")
    help_text: str | None = Field(default=None, description="Instructional text shown near the field explaining how to fill it in")
    options: list[str] | None = Field(default=None, description="For dropdown, radio, checkbox only — list all options exactly as shown on the form")
    standard_field_key: str | None = Field(default=None, description="The matching standard field key e.g. 'first_name', 'date_of_birth'. Null only if genuinely domain-specific with no standard mapping")
    standard_field_reason: str | None = Field(default=None, description="Why this mapping was chosen e.g. 'DOB is a common abbreviation for date_of_birth' or 'Address split into address_line_1 and address_line_2'")


class ExtractedSection(BaseModel):
    name: str = Field(description="Clear descriptive section name e.g. 'Personal Information', 'Banking Details'")
    description: str | None = Field(default=None, description="One sentence describing what this section contains")
    page_start: int = Field(description="First page this section appears on (1-indexed)")
    page_end: int = Field(description="Last page this section appears on (1-indexed)")
    is_continuation: bool = Field(default=False, description="True if this section continues from the left context page — its fields will be merged with the previous batch's last section")
    fields: list[ExtractedField] = Field(description="All fields extracted from this section in order")


class BatchExtractionResult(BaseModel):
    sections: list[ExtractedSection] = Field(description="All sections found in the extraction pages, in order")


# =============================================================================
# Agent
# =============================================================================

provider = GoogleProvider(api_key=settings.gemini_api_key.get_secret_value())
model_settings = GoogleModelSettings(
    google_thinking_config={
        "include_thoughts": True,
        "thinking_budget": -1,
    }
)
google_model = GoogleModel("gemini-3.1-pro-preview", provider=provider)

extraction_agent = Agent(
    model=google_model,
    model_settings=model_settings,
    output_type=BatchExtractionResult,
    system_prompt=f"""
You are an expert at analysing PDF form images and extracting every field precisely.
You are also opinionated — you actively normalise fields to match a known standard schema.

## Field Types
Use only these types:
- short_text: single line text input
- long_text: multi-line / paragraph text
- email: email address
- phone: phone number
- date: any date field
- number: numeric only
- dropdown: select one from a dropdown list
- radio: mutually exclusive options shown as radio buttons
- checkbox: single yes/no tick box
- file_upload: file or document upload
- signature: signature field

## Standard Fields
You must map fields to these standard keys wherever possible.
Each entry shows the key, its field type, default options (if any), and what form labels typically map to it:

{STANDARD_FIELDS_CONTEXT}

## When Mapping to a Standard Field
When you set standard_field_key, you MUST also:
1. Use the field_type from the standard field definition — not your own judgement
2. Use the default_options if the standard field has them, unless the form explicitly
   shows different options — in that case use the form's options instead
3. Always populate standard_field_reason explaining why you chose this mapping

## Normalisation Rules — this is critical
You must actively reshape vague or combined fields to match the standard schema.
Do not just accept what the form says — interpret the intent and normalise:

- "Name" or "Full Name" → split into TWO fields: first_name + last_name
  Exception: only use full_name if the form explicitly wants a combined name entry
- "Address" or "Street Address" → split into TWO fields: address_line_1 + address_line_2
- "Contact" or "Contact Details" → split into TWO fields: email + phone
- "DOB", "Birth Date", "D.O.B" → date_of_birth
- "ID", "ID No", "Identity Number", "RSA ID" → id_number
- "Cell", "Cellphone", "Mobile" → mobile_phone
- "Tel", "Telephone", "Home Phone" → phone
- "Employer", "Company Name", "Place of Work" → employer_name
- "Position", "Designation", "Occupation" → job_title
- "Account No", "Account Number" → bank_account_number
- "Branch Code", "BSB" → bank_branch_code
- "Postal Code", "Post Code", "ZIP" → postal_code
- "Signature", "Sign here" → signature
- "Title", "Salutation", "Mr/Mrs" → title (dropdown)

When you split a field, use clean labels:
  "Address" → label: "Address Line 1", standard_field_key: "address_line_1"
               label: "Address Line 2", standard_field_key: "address_line_2"

If a field partially matches a standard field, still map it to the closest key
and adjust the label to be clean and consistent.

Only leave standard_field_key as null if the field is genuinely domain-specific
with no reasonable standard mapping — e.g. "Membership Number", "Policy Reference",
"Claim Number". When in doubt, map it.

## Extraction Rules
- Extract EVERY field — no input box, checkbox, radio button, or signature line may be skipped
- Do NOT merge fields that should stay separate
- You MAY split one vague field into multiple standard fields where it clearly makes sense
- Do NOT invent fields that are not visible on the form
- For tables with repeating rows, extract each cell as a separate field with context:
  e.g. "Dependent 1 - Name", "Dependent 1 - Date of Birth", "Dependent 2 - Name"
- Extract fields in order: top to bottom, left to right
- Trust the image — it is the source of truth

## Sections
Group fields into logical sections based on visual headers, separators, or content grouping.
Examples: "Personal Information", "Employment Details", "Banking Details", "Declaration"

Each section must include:
- name: clear descriptive name
- description: one sentence describing what it contains
- page_start: first page this section appears on
- page_end: last page this section appears on

## Page Numbers — critical
Each batch message will include an explicit page number mapping, e.g.:
  "Image 2 = document page 6"
  "Image 3 = document page 7"

You MUST use these document page numbers for page_start and page_end.
Do NOT count images from 1 yourself — the images in a batch are a window
into a larger document and their numbers will not start at 1.

## Context Pages
Some images will be marked as CONTEXT ONLY with their document page number noted.
Use them ONLY to understand whether a section continues across the batch boundary.
DO NOT extract any fields from context pages.
DO NOT include context page numbers in page_start or page_end of any section.

If the very first section in your extraction batch continues from a left context page,
set is_continuation: true on that section. Its fields will be merged with the
previous batch's last section automatically. All other sections should have
is_continuation: false.
""",
)


# =============================================================================
# Runner
# =============================================================================

async def run_batch(
    pages: list[dict],
    left_context_url: str | None,
    right_context_url: str | None,
) -> BatchExtractionResult:
    message_content: list[UserContent] = []
    prompt_parts = []

    image_index = 1  # 1-indexed position in the message

    if left_context_url:
        message_content.append(ImageUrl(url=left_context_url))
        prompt_parts.append(
            f"Image {image_index} is LEFT CONTEXT ONLY (document page {pages[0]['page_number'] - 1}) — "
            "use it to understand section boundaries but do NOT extract any fields from it."
        )
        image_index += 1

    page_labels = []
    for page in pages:
        message_content.append(ImageUrl(url=page["url"]))
        page_labels.append(f"Image {image_index} = document page {page['page_number']}")
        image_index += 1

    if right_context_url:
        message_content.append(ImageUrl(url=right_context_url))
        last_page = pages[-1]["page_number"]
        prompt_parts.append(
            f"Image {image_index} is RIGHT CONTEXT ONLY (document page {last_page + 1}) — "
            "use it to understand if a section continues beyond this batch "
            "but do NOT extract any fields from it."
        )

    prompt_parts.append(
        "Page number mapping for this batch:\n" + "\n".join(page_labels)
    )
    prompt_parts.append(
        "Use the document page numbers above for page_start and page_end in your output. "
        "Do NOT count from 1 — use the exact document page numbers provided."
    )

    message_content.append("\n\n".join(prompt_parts))

    result = await extraction_agent.run(message_content)
    return result.output

# =============================================================================
# Merge
# =============================================================================

def merge_batch_results(batches: list[BatchExtractionResult]) -> list[ExtractedSection]:
    """
    Merge sections across batches, handling continuations at batch boundaries.
    """
    merged: list[ExtractedSection] = []

    for batch in batches:
        for i, section in enumerate(batch.sections):
            if i == 0 and section.is_continuation and merged:
                merged[-1].fields.extend(section.fields)
                merged[-1].page_end = section.page_end
            else:
                merged.append(section)

    return merged