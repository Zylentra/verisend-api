import asyncio
import logging
import math
import tempfile
import json
from pathlib import Path

import httpx

from verisend.agents.extraction_agent import BatchExtractionResult, merge_batch_results, run_batch
from verisend.utils.pdf import extract_page_images
from verisend.workers.celery_app import celery_app
from verisend.utils.blob_storage import get_blob_storage_client
from verisend.settings import settings

import nest_asyncio

logger = logging.getLogger(__name__)

BATCH_SIZE = 5
nest_asyncio.apply()

OUTPUT_DIR = Path(__file__).parent.parent.parent / "extraction_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

@celery_app.task
def test_task(url: str):
    print(f"Worker received URL: {url}")
    response = httpx.get(url)
    print(f"Downloaded {len(response.content)} bytes from blob")
    print("Worker done!")
    
async def _run_all_batches(batch_inputs: list[dict]) -> list[BatchExtractionResult]:
    """Run all batches sequentially in a single event loop."""
    results = []
    for batch in batch_inputs:
        result = await run_batch(
            pages=batch["pages"],
            left_context_url=batch["left_context_url"],
            right_context_url=batch["right_context_url"],
        )
        results.append(result)
    return results
    
@celery_app.task
def extract_form(setup_id: str, pdf_url: str):
    logger.info("Starting extraction: setup=%s", setup_id)

    temp_path = None

    try:
        # ------------------------------------------------------------------
        # 1. Download PDF
        # ------------------------------------------------------------------
        logger.info("Downloading PDF...")
        with httpx.Client(timeout=60.0) as client:
            r = client.get(pdf_url)
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(r.content)
                temp_path = tmp.name

        # ------------------------------------------------------------------
        # 2. Convert to page images
        # ------------------------------------------------------------------
        logger.info("Converting pages...")
        page_images = extract_page_images(temp_path, dpi=150)
        total_pages = len(page_images)
        logger.info("Got %d pages", total_pages)

        # ------------------------------------------------------------------
        # 3. Upload page images to blob
        # ------------------------------------------------------------------
        logger.info("Uploading page images...")
        page_records = []

        with get_blob_storage_client() as blob_service:
            container = blob_service.get_container_client(settings.blob_storage_container_name)

            for page_num, image_data in enumerate(page_images, start=1):
                blob_path = f"setups/{setup_id}/pages/page_{page_num}.png"
                blob_client = container.get_blob_client(blob_path)
                blob_client.upload_blob(image_data, overwrite=True)
                page_records.append({"page_number": page_num, "url": blob_client.url})
                logger.info("Uploaded page %d: %s", page_num, blob_client.url)

        # ------------------------------------------------------------------
        # 4. Run extraction in batches with windowing
        # ------------------------------------------------------------------
        total_batches = math.ceil(total_pages / BATCH_SIZE)
        batch_results: list[BatchExtractionResult] = []

        batch_inputs = []
        for batch_index in range(total_batches):
            start = batch_index * BATCH_SIZE
            end = min(start + BATCH_SIZE, total_pages)
            batch_pages = page_records[start:end]

            left_context_url = page_records[start - 1]["url"] if start > 0 else None
            right_context_url = page_records[end]["url"] if end < total_pages else None

            page_range = f"{batch_pages[0]['page_number']}–{batch_pages[-1]['page_number']}"
            logger.info("Queuing batch %d/%d — pages %s", batch_index + 1, total_batches, page_range)

            batch_inputs.append({
                "pages": batch_pages,
                "left_context_url": left_context_url,
                "right_context_url": right_context_url,
            })

        batch_results = asyncio.run(_run_all_batches(batch_inputs))

        # ------------------------------------------------------------------
        # 5. Merge and print
        # ------------------------------------------------------------------
        merged_sections = merge_batch_results(batch_results)
        logger.info("Merged into %d sections", len(merged_sections))

        # Write to file so we can inspect the full output
        output = [
            {
                "name": s.name,
                "description": s.description,
                "page_start": s.page_start,
                "page_end": s.page_end,
                "fields": [f.model_dump() for f in s.fields],
            }
            for s in merged_sections
        ]

        output_path = OUTPUT_DIR / f"extraction_{setup_id}.json"
        output_path.write_text(json.dumps(output, indent=2))
        logger.info("Result written to %s", output_path)
        logger.info("Sections: %d, Total fields: %d", len(merged_sections), sum(len(s.fields) for s in merged_sections))

    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)