from typing import Literal

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from verisend.settings import settings


class ExtractedStyling(BaseModel):
    primary_color: str = Field(description="Main brand / primary action color as hex e.g. '#1A73E8'")
    accent_color: str = Field(description="Secondary / accent color used for highlights as hex")
    background_color: str = Field(description="Page background color as hex")
    surface_color: str = Field(description="Card / surface background color as hex")
    text_color: str = Field(description="Primary body text color as hex")
    label_color: str = Field(description="Form label / heading text color as hex")
    border_color: str = Field(description="Input border / divider color as hex")
    error_color: str = Field(description="Error / danger color as hex — default to a standard red if not found")
    font_family: str = Field(description="The ID (key) of the closest matching font from the provided available fonts dict")
    heading_size: Literal["sm", "md", "lg"] = Field(description="Relative heading size: sm, md, or lg")
    border_radius: Literal["none", "sm", "md", "lg", "full"] = Field(description="Border radius style inferred from the site's design")
    spacing: Literal["compact", "comfortable", "spacious"] = Field(description="Overall spacing density")
    button_style: Literal["filled", "outlined"] = Field(description="Whether buttons are primarily filled or outlined")


provider = GoogleProvider(api_key=settings.gemini_api_key.get_secret_value())
google_model = GoogleModel("gemini-2.5-flash", provider=provider)

styling_agent = Agent(
    model=google_model,
    output_type=ExtractedStyling,
    system_prompt=(
        "You are a design system analyst. Given the HTML of a website, extract the brand's "
        "visual identity and map it to a form styling configuration.\n\n"
        "Focus on:\n"
        "- Colors: look at CSS custom properties, inline styles, class names, and computed styles. "
        "Identify the primary brand color, accent color, background, surface (cards/panels), "
        "text, label, border, and error colors.\n"
        "- Typography: identify the primary font family from font-face declarations, CSS, or Google Fonts links. "
        "You will be given a list of available fonts — pick the closest match from that list.\n"
        "- Spacing & shape: infer whether the design is compact/comfortable/spacious and "
        "whether it uses rounded corners (and how much).\n"
        "- Buttons: determine if the primary button style is filled or outlined.\n\n"
        "All colors must be returned as hex values (e.g. '#1A73E8').\n"
        "If a value cannot be confidently determined, use a sensible default that fits the overall palette.\n"
        "Do not hallucinate — base your answers on what is actually in the HTML/CSS."
    ),
)


async def extract_styling_from_url(url: str, available_fonts: dict[str, str]) -> ExtractedStyling:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()

    # Trim to a reasonable size for the LLM context
    html = response.text[:50_000]
    fonts = "\n".join(f"- ID: {k}, Name: {v}" for k, v in available_fonts.items())

    result = await styling_agent.run(
        f"Available fonts (return the ID of the closest match):\n{fonts}\n\n"
        f"Extract the styling/brand identity from this website HTML:\n\n{html}"
    )
    return result.output
