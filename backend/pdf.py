import fitz  # PyMuPDF
import logging

logger = logging.getLogger(__name__)

def extract_pdf_text(file_bytes: bytes) -> str:
    """
    Extracts all text from a PDF file provided as bytes using PyMuPDF.
    
    Args:
        file_bytes (bytes): The raw bytes of the uploaded PDF file.
        
    Returns:
        str: Extracted text from all pages of the PDF.
    """
    logger.info("Extracting text from PDF bytes using PyMuPDF...")
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        
        extracted_len = len(text)
        logger.info(f"Successfully extracted {extracted_len} characters from PDF.")
        return text
    except Exception as e:
        logger.error(f"Failed to extract text from PDF: {str(e)}")
        raise e
