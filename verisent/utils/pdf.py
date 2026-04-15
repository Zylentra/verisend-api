import fitz
import asyncio


def extract_page_images(file_path: str, dpi: int) -> list[bytes]:
    """
    Render each page of a PDF as a PNG image.
    
    Args:
        file_path: Path to the PDF file
        dpi: Resolution for rendering (150 is good balance of quality/size)
        
    Returns:
        List of PNG image bytes, one per page
    """
    doc = fitz.open(file_path)
    
    image_bytes = []
    for page in doc:
        # Render page to pixmap
        pix = page.get_pixmap(dpi=dpi)
        image_bytes.append(pix.tobytes("png"))
    
    doc.close()
    return image_bytes


async def extract_page_images_async(file_path: str, dpi: int = 300) -> list[bytes]:
    """Async wrapper for extract_page_images."""
    return await asyncio.to_thread(extract_page_images, file_path, dpi)