import asyncio
import logging
import math
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from sqlmodel import Session, select

from verisend.agents.extraction_agent import BatchExtractionResult, merge_batch_results, run_batch
from verisend.utils.pdf import extract_page_images
from verisend.workers.celery_app import celery_app
from verisend.utils.blob_storage import get_blob_storage_client
from verisend.utils.db import sync_engine
from verisend.models.db_models import Form, FormImage, FormSection, ProcessingJob, JobStatus, StandardField
from verisend.settings import settings

logger = logging.getLogger(__name__)

BATCH_SIZE = 5

OUTPUT_DIR = Path(__file__).parent.parent.parent / "extraction_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def _update_job(session: Session, job: ProcessingJob, **kwargs):
    for key, value in kwargs.items():
        setattr(job, key, value)
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()


@celery_app.task
def test_task(url: str):
    print(f"Worker received URL: {url}")
    response = httpx.get(url)
    print(f"Downloaded {len(response.content)} bytes from blob")
    print("Worker done!")


async def _run_all_batches(batch_inputs: list[dict], standard_fields: list[StandardField]) -> list[BatchExtractionResult]:
    """Run all batches sequentially in a single event loop."""
    results = []
    for batch in batch_inputs:
        result = await run_batch(
            pages=batch["pages"],
            left_context_url=batch["left_context_url"],
            right_context_url=batch["right_context_url"],
            standard_fields=standard_fields,
        )
        results.append(result)
    return results


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def extract_form(self, job_id: str, form_id: str, pdf_url: str, summary: str | None, context: str | None):
    import nest_asyncio
    nest_asyncio.apply()

    logger.info("Starting extraction: form=%s", form_id)
    temp_path = None

    with Session(sync_engine) as session:
        job = session.get(ProcessingJob, UUID(job_id))
        form = session.get(Form, UUID(form_id))

        if not job or not form:
            logger.error("Job or form not found: job=%s form=%s", job_id, form_id)
            return

        try:
            # ------------------------------------------------------------------
            # 1. Download PDF
            # ------------------------------------------------------------------
            _update_job(session, job, status=JobStatus.PROCESSING.value, current_step="Downloading document", progress=5)

            with httpx.Client(timeout=60.0) as client:
                r = client.get(pdf_url)
                r.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(r.content)
                    temp_path = tmp.name

            # ------------------------------------------------------------------
            # 2. Convert to page images
            # ------------------------------------------------------------------
            _update_job(session, job, current_step="Converting pages", progress=10)

            page_images = extract_page_images(temp_path, dpi=150)
            total_pages = len(page_images)
            logger.info("Got %d pages", total_pages)

            # ------------------------------------------------------------------
            # 3. Upload page images to blob
            # ------------------------------------------------------------------
            _update_job(session, job, current_step="Uploading page images", progress=15)

            page_records = []

            with get_blob_storage_client() as blob_service:
                container = blob_service.get_container_client(settings.blob_storage_container_name)

                for page_num, image_data in enumerate(page_images, start=1):
                    blob_path = f"forms/{form_id}/pages/page_{page_num}.png"
                    blob_client = container.get_blob_client(blob_path)
                    blob_client.upload_blob(image_data, overwrite=True)

                    db_image = FormImage(
                        form_id=UUID(form_id),
                        page_number=page_num,
                        image_url=blob_client.url,
                    )
                    session.add(db_image)
                    page_records.append({"page_number": page_num, "url": blob_client.url})
                    logger.info("Uploaded page %d: %s", page_num, blob_client.url)

            session.commit()

            # ------------------------------------------------------------------
            # 4. Run extraction in batches with windowing
            # ------------------------------------------------------------------
            total_batches = math.ceil(total_pages / BATCH_SIZE)
            batch_inputs = []

            for batch_index in range(total_batches):
                start = batch_index * BATCH_SIZE
                end = min(start + BATCH_SIZE, total_pages)
                batch_pages = page_records[start:end]

                left_context_url = page_records[start - 1]["url"] if start > 0 else None
                right_context_url = page_records[end]["url"] if end < total_pages else None

                page_range = f"{batch_pages[0]['page_number']}–{batch_pages[-1]['page_number']}"
                progress = 20 + int((batch_index / total_batches) * 70)

                _update_job(
                    session, job,
                    current_step=f"Extracting pages {page_range} (batch {batch_index + 1} of {total_batches})",
                    progress=progress,
                )

                logger.info("Queuing batch %d/%d — pages %s", batch_index + 1, total_batches, page_range)

                batch_inputs.append({
                    "pages": batch_pages,
                    "left_context_url": left_context_url,
                    "right_context_url": right_context_url,
                })

            standard_fields = list(session.exec(select(StandardField)).all())
            if not standard_fields:
                raise Exception("No standard fields found in database. Seed them via POST /admin/standard-fields before running extraction.")
            batch_results = asyncio.run(_run_all_batches(batch_inputs, standard_fields))

            # ------------------------------------------------------------------
            # 5. Merge sections
            # ------------------------------------------------------------------
            _update_job(session, job, current_step="Merging sections", progress=92)

            merged_sections = merge_batch_results(batch_results)
            logger.info("Merged into %d sections", len(merged_sections))

            # ------------------------------------------------------------------
            # 6. Save sections to DB
            # ------------------------------------------------------------------
            _update_job(session, job, current_step="Saving results", progress=95)

            for i, section in enumerate(merged_sections, start=1):
                db_section = FormSection(
                    form_id=UUID(form_id),
                    section_number=i,
                    name=section.name,
                    description=section.description,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    fields=[f.model_dump() for f in section.fields],
                )
                session.add(db_section)

            form.updated_at = datetime.now(timezone.utc)
            session.add(form)

            _update_job(session, job, status=JobStatus.DONE.value, current_step="Done", progress=100)

            logger.info("Extraction complete: form=%s sections=%d", form_id, len(merged_sections))

        except Exception as exc:
            logger.exception("Extraction failed for form=%s", form_id)
            _update_job(session, job, status=JobStatus.FAILED.value, current_step="Failed", error=str(exc), progress=0)
            raise self.retry(exc=exc)

        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)