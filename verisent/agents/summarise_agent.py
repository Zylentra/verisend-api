from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.messages import ImageUrl, UserContent

from verisend.settings import settings


class SummariseResult(BaseModel):
    name: str = Field(description="A clean concise name for this form e.g. 'GEMS Medical Aid Application Form'")
    summary: str = Field(description="2-3 sentences describing what this form is, what it is used for, and what kind of information it collects")


provider = GoogleProvider(api_key=settings.gemini_api_key.get_secret_value())
google_model = GoogleModel("gemini-2.5-flash", provider=provider)

summarise_agent = Agent(
    model=google_model,
    output_type=SummariseResult,
    system_prompt=(
        "You are analysing a PDF form to help a user digitise it. "
        "Determine a clean descriptive name for the form and write a concise summary. "
        "Be plain and factual — no marketing language."
    ),
)


async def summarise_form(pdf_url: str) -> SummariseResult:
    message_content: list[UserContent] = [
        ImageUrl(url=pdf_url),
        "What is this form called and what does it collect?",
    ]
    result = await summarise_agent.run(message_content)
    return result.output