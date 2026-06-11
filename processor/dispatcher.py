from .pdf_processor import process_pdf
from .word_processor import process_word
from .excel_processor import process_excel
from .pptx_processor import process_pptx

SUPPORTED_TYPES = {
    "pdf": process_pdf,
    "docx": process_word,
    "doc": process_word,
    "xlsx": process_excel,
    "xls": process_excel,
    "pptx": process_pptx,
    "ppt": process_pptx,
}

def dispatch(doc_id: int, file_path: str, file_type: str) -> dict:
    file_type = file_type.lower().lstrip(".")
    processor = SUPPORTED_TYPES.get(file_type)
    if not processor:
        raise ValueError(f"Tipe file tidak didukung: {file_type}")
    return processor(doc_id, file_path)
