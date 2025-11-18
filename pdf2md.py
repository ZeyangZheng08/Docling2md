import logging
import time
import base64
import json
import yaml
import hashlib
import re
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from pdf2image import convert_from_path
import pandas as pd
from PIL import Image
from openai import OpenAI
from docling_core.types.doc import PictureItem, TableItem
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from prompt.VLM_prompt import VLM_PROMPT
from prompt.text_type_prompt import TEXT_TYPE_PROMPT
from prompt.table_repair_prompt import TABLE_REPAIR_PROMPT
from prompt.text_repair_prompt import TEXT_REPAIR_PROMPT

# Load configuration file (located next to this script)
script_dir = Path(__file__).parent
config_path = script_dir / 'config.yaml'
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# === Configuration === (from configuration file)
ENABLE_OCR = config['OCR']['enabled']
DASHSCOPE_API_KEY = config['VLM']['api_key']
VLM_API_URL = config['VLM']['base_url']
VLM_MODEL = config['VLM']['model']
TEXT_API_KEY = config['OPENAI']['api_key']
TEXT_API_URL = config['OPENAI']['base_url']
TEXT_MODEL = config['OPENAI']['model']
MAX_CONCURRENCY_VLM = config['VLM']['max_concurrency']
MAX_CONCURRENCY_TEXT = config['OPENAI']['max_concurrency']

input_pdf_dir = Path("/home/zeyang/Zeyang/AI_Agent/Building_inspection_agent/github/test")


def generate_hash_from_file(file_path: Path) -> str:
    md5_hash = hashlib.md5()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


# === Convert PDF pages to images ===
def convert_pdf_to_images(pdf_path: Path, output_dir: Path):
    # Get Poppler path from configuration
    poppler_path = Path(config['POPPLER']['path'])
    # create a `page` subdirectory
    page_dir = output_dir / "page"
    page_dir.mkdir(parents=True, exist_ok=True)
    # Use pdf2image to convert each page into an image
    pages = convert_from_path(
        pdf_path,
        dpi=300,  # 300 DPI
        poppler_path=str(poppler_path)
    )
    for page_num, page in enumerate(pages, start=1):
        page_image_filename = page_dir / f"page-{page_num}.png"
        page.save(page_image_filename, 'PNG')
        log.info(f"Saved PDF page {page_num}: {page_image_filename.resolve()}")


# === Image + Prompt → Markdown table (VLM) ===
def ask_table_from_image(pil_image: Image.Image, prompt: str = TABLE_REPAIR_PROMPT) -> str:
    try:
        buffered = BytesIO()
        pil_image.save(buffered, format="JPEG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=VLM_API_URL)
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}
        ]
        completion = client.chat.completions.create(
            model=VLM_MODEL,
            messages=[{"role": "user", "content": content}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        log.warning(f"❌ Table image repair failed: {e}")
        return "[Table repair failed]"


# === Image description ===
def ask_image_vlm_base64(pil_image: Image.Image, prompt: str = VLM_PROMPT) -> str:
    try:
        buffered = BytesIO()
        pil_image.save(buffered, format="JPEG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
        client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=VLM_API_URL)
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"}}
        ]
        completion = client.chat.completions.create(
            model=VLM_MODEL,
            messages=[{"role": "user", "content": content}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        log.warning(f"Image API failed: {e}")
        return "[Image description failed]"

def needs_repair(text: str, threshold: int = 30) -> bool:
    # Match long continuous English-like sequences (no spaces or CJK)
    matches = re.findall(r'[A-Za-z0-9,.\-()]{%d,}' % threshold, text)
    return len(matches) > 0


# === English segmentation repair (large model) ===
def ask_repair_text(text: str) -> str:
    try:
        client = OpenAI(api_key=TEXT_API_KEY, base_url=TEXT_API_URL)
        prompt = f"{TEXT_REPAIR_PROMPT}\n{text}"
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        repaired = response.choices[0].message.content.strip()
        return repaired
    except Exception as e:
        log.warning(f"❌ English segmentation failed: {e}")
        return text  # Return original text on failure

# === Determine text type (heading or paragraph) ===
def ask_if_heading(text: str) -> str:
    try:
        client = OpenAI(api_key=TEXT_API_KEY, base_url=TEXT_API_URL)
        prompt = f"{TEXT_TYPE_PROMPT}\n{text}"
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        answer = response.choices[0].message.content.strip().lower()
        return "heading" if "heading" in answer else "paragraph"
    except Exception as e:
        log.warning(f"Failed to determine heading/body: {e}")
        return "paragraph"

# === Split table image into rows (fixed row height) ===
def split_table_image_rows(pil_img: Image.Image, row_height: int = 400) -> list:
    width, height = pil_img.size
    slices = []
    for top in range(0, height, row_height):
        bottom = min(top + row_height, height)
        crop = pil_img.crop((0, top, width, bottom))
        slices.append(crop)
    return slices


# === Merge small chunks that don't meet size limits ===
def merge_small_chunks(chunks: list, min_height: int = 300, min_width: int = 20) -> list:
    merged_chunks = []
    temp_chunk = None

    for chunk in chunks:
        width, height = chunk.size

        # If the current chunk is too small, try to concatenate it with the next/previous
        if height < min_height or width < min_width:
            if temp_chunk is None:
                temp_chunk = chunk
            else:
                # concatenate vertically
                new_chunk = Image.new("RGB", (max(temp_chunk.width, chunk.width), temp_chunk.height + chunk.height))
                new_chunk.paste(temp_chunk, (0, 0))
                new_chunk.paste(chunk, (0, temp_chunk.height))
                temp_chunk = new_chunk
        else:
            # if there is an unprocessed temp chunk, save it first
            if temp_chunk is not None:
                merged_chunks.append(temp_chunk)
                temp_chunk = None
            merged_chunks.append(chunk)

    # add the last temp chunk (if any)
    if temp_chunk is not None:
        # if the whole table image height is below minimum, pad to minimum height
        if temp_chunk.height < min_height:
            new_chunk = Image.new("RGB", (temp_chunk.width, max(temp_chunk.height, 20)))
            new_chunk.paste(temp_chunk, (0, 0))
            merged_chunks.append(new_chunk)
        else:
            merged_chunks.append(temp_chunk)

    return merged_chunks


# === Get element bounding box ===
def get_bbox(element):
    if hasattr(element, 'prov') and element.prov:
        bbox = element.prov[0].bbox
        return {
            "left": bbox.l,
            "top": bbox.t,
            "right": bbox.r,
            "bottom": bbox.b,
            "coord_origin": bbox.coord_origin
        }
    return None


# === Main process ===
def convert_pdf_to_markdown_with_images(input_pdf_path: Path):
    start_time = time.time()

    # Get hash from file to name output subdirectory
    pdf_hash = generate_hash_from_file(input_pdf_path)
    output_dir = Path.cwd() / "output" / pdf_hash
    output_dir.mkdir(parents=True, exist_ok=True)
    doc_filename = input_pdf_path.stem

    pipeline_options = PdfPipelineOptions()
    pipeline_options.images_scale = 2.0
    pipeline_options.generate_picture_images = True
    pipeline_options.generate_table_images = True
    if ENABLE_OCR:
        pipeline_options.do_ocr = True
        pipeline_options.ocr_options = RapidOcrOptions(force_full_page_ocr=True)
    doc_converter = DocumentConverter(
        format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
    )
    conv_res = doc_converter.convert(input_pdf_path)
    document = conv_res.document

    markdown_lines_items = []  # Fix: append by element order
    json_data = []
    table_counter = 0
    picture_counter = 0

    convert_pdf_to_images(input_pdf_path, output_dir)

    vlm_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY_VLM)
    text_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY_TEXT)
    futures = []

    for element, level in document.iterate_items():
        bbox = get_bbox(element)
        if isinstance(element, TableItem):
            table_counter += 1
            table_image_filename = output_dir / f"{pdf_hash}-table-{table_counter}.png"
            pil_img = element.get_image(document)
            pil_img.save(table_image_filename, "PNG")
            table_df: pd.DataFrame = element.export_to_dataframe()
            if not table_df.columns.is_unique or table_df.shape[1] < 2:
                log.warning(f"\u26a0\ufe0f Table {table_counter} structure abnormal, using Qwen multi-step VLM repair")
                sub_images = split_table_image_rows(pil_img)
                sub_images = merge_small_chunks(sub_images)
                chunk_futures = []
                for idx, chunk_img in enumerate(sub_images):
                    future = vlm_executor.submit(ask_table_from_image, chunk_img)
                    chunk_futures.append((future, idx, chunk_img))
                full_md_lines = []
                for future, idx, chunk_img in chunk_futures:
                    try:
                        chunk_md = future.result()
                        lines = chunk_md.splitlines()
                        if idx == 0:
                            full_md_lines.extend(lines)
                        else:
                            full_md_lines.extend(lines[2:])
                    except Exception as e:
                        log.warning(f"Table chunk processing failed: {e}")
                markdown = f"<!-- Table {table_counter} repaired using VLM -->\n" + "\n".join(full_md_lines)
                markdown_lines_items.append(markdown)
                json_data.append({
                    "type": "table",
                    "level": level,
                    "image": table_image_filename.name,
                    "source": "reconstructed_by_qwen_chunked",
                    "markdown": "\n".join(full_md_lines),
                    "page_number": element.prov[0].page_no,
                    "bbox": bbox
                })
                continue
            markdown = table_df.to_markdown(index=False)
            markdown_lines_items.append(markdown)
            json_data.append({
                "type": "table",
                "level": level,
                "image": table_image_filename.name,
                "data": table_df.to_dict(orient="records"),
                "page_number": element.prov[0].page_no,
                "bbox": bbox
            })

        elif isinstance(element, PictureItem):
            picture_counter += 1
            picture_image_filename = output_dir / f"{pdf_hash}-picture-{picture_counter}.png"
            pil_img = element.get_image(document)
            pil_img.save(picture_image_filename, "PNG")
            future = vlm_executor.submit(ask_image_vlm_base64, pil_img)
            futures.append((future, "picture", {
                "image_path": picture_image_filename,
                "level": level,
                "page": element.prov[0].page_no,
                "bbox": bbox
            }))
            markdown_lines_items.append(future)  # placeholder
        else:
            if hasattr(element, "text") and element.text:
                text = element.text.strip()
                if text:
                    if needs_repair(text):
                        log.info(f"Detected abnormal no-space segment, calling segmentation repair: {text}")
                        text = ask_repair_text(text)
                    future = text_executor.submit(ask_if_heading, text)
                    futures.append((future, "text", {
                        "text": text,
                        "level": level,
                        "page": element.prov[0].page_no,
                        "bbox": bbox
                    }))
                    markdown_lines_items.append(future)

    results_map = {}
    for future, task_type, meta in futures:
        try:
            result = future.result()
            if task_type == "picture":
                caption = result
                results_map[future] = f"![{caption}](./{meta['image_path'].name})"
                json_data.append({
                    "type": "picture",
                    "level": meta["level"],
                    "image": meta["image_path"].name,
                    "caption": caption,
                    "page_number": meta["page"],
                    "bbox": meta["bbox"]
                })
            elif task_type == "text":
                label = result
                markdown = f"# {meta['text']}" if label == "heading" else meta["text"]
                results_map[future] = markdown
                json_data.append({
                    "type": "text",
                    "level": meta["level"],
                    "text": meta["text"],
                    "label": label,
                    "page_number": meta["page"],
                    "bbox": meta["bbox"]
                })
        except Exception as e:
            log.warning(f"Concurrent task failed: {e}")

    vlm_executor.shutdown(wait=True)
    text_executor.shutdown(wait=True)

    markdown_lines = []
    for item in markdown_lines_items:
        if isinstance(item, str):
            markdown_lines.append(item)
            markdown_lines.append("")
        elif hasattr(item, "result"):
            markdown_lines.append(results_map.get(item, ""))
            markdown_lines.append("")

    markdown_file = output_dir / f"{pdf_hash}.md"
    with markdown_file.open("w", encoding="utf-8") as f:
        f.write("\n".join(markdown_lines))

    json_file = output_dir / f"{pdf_hash}.json"
    with json_file.open("w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    log.info(f"Completed PDF parsing, took {time.time() - start_time:.2f} seconds")
    log.info(f"Markdown file: {markdown_file.resolve()}")
    log.info(f"JSON file: {json_file.resolve()}")


# === Batch processing function ===
def process_multiple_pdfs():
    """Process all PDF files in the input directory"""

    batch_start_time = time.time()

    # Find all PDF files in the directory
    pdf_files = list(input_pdf_dir.glob("*.pdf"))

    if not pdf_files:
        log.warning(f"No PDF files found in {input_pdf_dir}")
        return

    log.info(f"Found {len(pdf_files)} PDF files to process")

    # Process each PDF file
    for pdf_file in pdf_files:
        log.info(f"Processing PDF: {pdf_file.name}")
        try:
            # Call the existing conversion function for each PDF
            convert_pdf_to_markdown_with_images(pdf_file)
        except Exception as e:
            log.error(f"Failed to process {pdf_file.name}: {e}")
            continue

    total_time = time.time() - batch_start_time
    log.info(f"Batch processing completed. Total time: {total_time:.2f} seconds")


if __name__ == "__main__":
    process_multiple_pdfs()
