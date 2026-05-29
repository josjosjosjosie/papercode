import os
import subprocess
from datetime import datetime
import tempfile
import time
import shutil
import ast
import re


class _TeeStream:
    def __init__(self, stream):
        self._stream = stream
        self._buffer = []

    def write(self, text):
        self._buffer.append(text)
        return self._stream.write(text)

    def flush(self):
        return self._stream.flush()

    def getvalue(self):
        return ''.join(self._buffer)

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    import twain
except Exception:
    twain = None

try:
    from google import genai
except Exception:
    genai = None

try:
    from google.genai import types as genai_types
except Exception:
    genai_types = None

try:
    import black
except Exception:
    black = None

try:
    import jsbeautifier
except Exception:
    jsbeautifier = None

try:
    from PIL import Image, ImageOps, ImageFilter
except Exception:
    Image = None
    ImageOps = None
    ImageFilter = None

# Extra imports for the styled A4 printing functionality
try:
    import win32api
except Exception:
    win32api = None

try:
    import win32print
except Exception:
    win32print = None

try:
    from tqdm import tqdm
except Exception:
    tqdm = None


THINKING_UNSUPPORTED_MODELS = set()


class ThinkingBudgetUnsupported(Exception):
    pass


def _progress_simulator(step_name, seconds=3):
    if tqdm is not None:
        for _ in tqdm(range(seconds), desc=step_name, unit='s'):
            time.sleep(1)
    else:
        print(f"{step_name}...", end='', flush=True)
        for _ in range(seconds):
            print('.', end='', flush=True)
            time.sleep(1)
        print(' done')


def list_scanners_windows():
    if twain is None:
        return []
    try:
        sm = twain.SourceManager(0)
        return sm.source_list
    except Exception:
        return []


def list_printers_windows():
    printers = []
    default = None
    if win32print is not None:
        try:
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            for p in win32print.EnumPrinters(flags):
                printers.append(p[2])
            try:
                default = win32print.GetDefaultPrinter()
            except Exception:
                default = None
            return printers, default
        except Exception:
            return [], None
    # Fallback: use WMIC
    if shutil.which('wmic'):
        try:
            proc = subprocess.run(['wmic', 'printer', 'get', 'name'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            out = proc.stdout.decode(errors='ignore')
            for line in out.splitlines():
                line = line.strip()
                if line and line.lower() != 'name':
                    printers.append(line)
            return printers, None
        except Exception:
            return [], None
    return [], None

try:
    from pygments.lexers import guess_lexer, get_lexer_by_name
    from pygments.token import Keyword, Name, Comment, String, Literal, Number, Operator
except Exception:
    guess_lexer = None

try:
    from reportlab.lib.pagesizes import a4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, XPreformatted, PageBreak, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
except Exception:
    a4 = None

def _escape_html_preserve_spaces(text):
    return (
        text.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _colorize_code(code_text):
    try:
        lexer_obj = guess_lexer(code_text)
    except Exception:
        lexer_obj = get_lexer_by_name("python")

    color_map = {
        Keyword: "#7A1FA2",
        Name.Function: "#0047AB",
        Name.Class: "#1B5E20",
        Name.Builtin: "#0047AB",
        String: "#0B8043",
        Comment: "#757575",
        Number: "#C65D00",
        Operator: "#8B0000",
        Name: "#000000",
    }

    formatted_code = ""
    for token_type, value in lexer_obj.get_tokens(code_text):
        safe = _escape_html_preserve_spaces(value)
        safe = safe.replace('\t', '&nbsp;&nbsp;&nbsp;&nbsp;').replace(' ', '&nbsp;').replace('\n', '<br/>')
        base_type = token_type
        while base_type not in color_map and getattr(base_type, 'parent', None):
            base_type = base_type.parent
        color = color_map.get(base_type, "#000000")
        if color == "#000000":
            formatted_code += safe
        else:
            formatted_code += f'<font color="{color}">{safe}</font>'

    return formatted_code


def make_styled_pdf(code_text, report_lines=None, include_report_page=False, out_pdf_path=None):
    if guess_lexer is None or a4 is None:
        print("Missing dependencies for PDF generation (pygments/reportlab). Skipping PDF creation.")
        return None

    if out_pdf_path is None:
        out_pdf_path = os.path.join(tempfile.gettempdir(), "ocr_print_job.pdf")

    formatted_code = _colorize_code(code_text)

    doc = SimpleDocTemplate(out_pdf_path, pagesize=a4, leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    code_style = ParagraphStyle(
        'CodeStyle',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8.6,
        leading=10.2,
        textColor='#000000',
        spaceBefore=0,
        spaceAfter=0,
        leftIndent=0,
        rightIndent=0,
        firstLineIndent=0,
    )
    report_style = ParagraphStyle(
        'ReportStyle',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=9,
        leading=11,
        textColor='#000000',
        spaceBefore=0,
        spaceAfter=0,
    )
    heading_style = ParagraphStyle(
        'HeadingStyle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        textColor='#000000',
        spaceAfter=8,
    )

    story = [XPreformatted(formatted_code, code_style)]
    if include_report_page:
        report_text = _escape_html_preserve_spaces('\n'.join(report_lines or ['No report data available.']))
        story.extend([
            PageBreak(),
            Paragraph('Scan / OCR Report', heading_style),
            Spacer(1, 8),
            XPreformatted(report_text, report_style),
        ])

    try:
        doc.build(story)
        return out_pdf_path
    except Exception as e:
        print(f"Failed to build PDF: {e}")
        return None


def scan_to_highest_dpi(device=None):
    # 1. Base path where you want to store the scans
    # WARNING: Change this to a path that exists on your system
    base_path = r"C:\Scans" 

    # 2. Create the date-based folder structure: YYYY/MM/DD
    now = datetime.now()
    date_folder = os.path.join(base_path, now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"))
    
    # Create the directories if they don't exist yet
    os.makedirs(date_folder, exist_ok=True)

    # 3. Create the filename: HH-MM-SS.png (Windows does not allow : or / in filenames)
    file_name = now.strftime("%H-%M-%S.png")
    
    # This is the variable you requested, holding the absolute file path
    scanned_image = os.path.join(date_folder, file_name)

    print(f"Preparing to scan to: {scanned_image}")

    # 4. Connect to the Source Manager (TWAIN)
    if twain is None:
        print("TWAIN module not available. Install a TWAIN wrapper for Python to scan on Windows.")
        return None

    # 0 indicates the parent window handle (none in this headless case)
    sm = twain.SourceManager(0)
    sources = sm.source_list

    if not sources:
        print("No scanners found. Please check your connection or drivers.")
        return None

    # Choose scanner
    default_scanner = device or sources[0]
    print(f"Using scanner: {default_scanner}")

    # 5. Find the highest supported DPI
    max_dpi = 600 # Fallback default in case the scanner doesn't report capabilities properly
    try:
        # Open the source temporarily to check capabilities
        src = sm.open_source(default_scanner)
        if src:
            # 0x1118 is the TWAIN constant for ICAP_XRESOLUTION (DPI)
            cap_type, cap_value = src.get_capability(0x1118)
            
            # TWAIN returns capabilities in different formats (dict/range, list/enum, or single value)
            if isinstance(cap_value, dict) and 'MaxValue' in cap_value:
                max_dpi = cap_value['MaxValue']
            elif isinstance(cap_value, (list, tuple)):
                max_dpi = max(cap_value)
            else:
                max_dpi = float(cap_value)
            
            src.close()
    except Exception as e:
        print(f"Could not automatically determine max DPI, defaulting to {max_dpi}. Error: {e}")

    if max_dpi < 600:
        max_dpi = 600

    print(f"Scanning at {max_dpi} DPI... Please wait.")
    _progress_simulator('Scanning', seconds=2)
    scan_to_highest_dpi.last_dpi = max_dpi
    scan_to_highest_dpi.last_dpi = max_dpi

    # 6. Acquire the image without UI
    try:
        result = twain.acquire(
            path=scanned_image,
            ds_name=default_scanner,
            dpi=max_dpi,
            pixel_type='color', # Options: 'color', 'gray', 'bw'
            show_ui=False,      # Disables the scanner's popup UI
            modal=False
        )

        if result:
            print(f"Image saved to {scanned_image}")
            return scanned_image
        else:
            print("Scan was cancelled or failed.")
            return None

    except Exception as e:
        print(f"An error occurred during scanning: {e}")
        return None


def _beautify_code_text(code_text):
    cleaned = (code_text or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    try:
        lexer = guess_lexer(cleaned) if guess_lexer is not None else None
    except Exception:
        lexer = None

    lexer_name = getattr(lexer, "name", "").lower() if lexer is not None else ""

    if black is not None and "python" in lexer_name:
        try:
            return black.format_str(cleaned + "\n", mode=black.FileMode()).rstrip()
        except Exception as e:
            print(f"Black formatting failed, using fallback cleanup: {e}")

    if jsbeautifier is not None and lexer_name in {"javascript", "typescript", "json", "html", "xml", "css"}:
        try:
            return jsbeautifier.beautify(cleaned)
        except Exception as e:
            print(f"JS beautifier failed, using fallback cleanup: {e}")

    return cleaned


def _syntax_status(code_text):
    try:
        lexer = guess_lexer(code_text) if guess_lexer is not None else None
    except Exception:
        lexer = None

    lexer_name = getattr(lexer, "name", "").lower() if lexer is not None else ""
    if "python" not in lexer_name:
        return [f"Syntax check skipped for detected language: {lexer_name or 'unknown'}"]

    try:
        ast.parse(code_text)
        return ["Python syntax check: OK"]
    except SyntaxError as exc:
        return [f"Python syntax check error: {exc.msg} at line {exc.lineno}"]


def _gemini_generate(client, model_name, prompt, thinking_budget, max_attempts=15):
    if model_name in THINKING_UNSUPPORTED_MODELS or thinking_budget is None or genai_types is None:
        config = None
    else:
        config = genai_types.GenerateContentConfig(
            temperature=0.1,
            topP=0.1,
            maxOutputTokens=8192,
            responseMimeType="text/plain",
            thinkingConfig=genai_types.ThinkingConfig(
                includeThoughts=False,
                thinkingBudget=thinking_budget,
            ),
        )
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            if config is not None:
                response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            else:
                response = client.models.generate_content(model=model_name, contents=prompt)

            if hasattr(response, 'text') and response.text:
                return response.text.strip()
            return ""
        except Exception as e:
            last_error = e
            error_text = str(e)
            unsupported_thinking = (
                'thinking budget is not supported for this model' in error_text.lower()
                or 'thinking budget' in error_text.lower() and 'unsupported' in error_text.lower()
                or ('400' in error_text and 'INVALID_ARGUMENT' in error_text.upper())
            )
            if unsupported_thinking and config is not None:
                THINKING_UNSUPPORTED_MODELS.add(model_name)
                raise ThinkingBudgetUnsupported(model_name)

            is_unavailable = (
                '503' in error_text
                or 'UNAVAILABLE' in error_text.upper()
                or 'high demand' in error_text.lower()
            )
            if not is_unavailable or attempt == max_attempts:
                raise

            wait_seconds = min(60, 2 ** min(attempt, 5))
            print(f"Gemini is busy (503/UNAVAILABLE). Retrying in {wait_seconds}s... ({attempt}/{max_attempts})")
            time.sleep(wait_seconds)

    if last_error is not None:
        raise last_error
    return ""


def _build_gemini_prompt(title, code_text):
    return (
        f"You are an expert programmer. Your ONLY task is to repair the code below. "
        f"Fix only syntax errors, logical bugs, OCR mistakes, and crashes that prevent it from working as intended. "
        f"Do not refactor, do not rename variables, do not add explanations, do not add comments, and do not change the overall structure unless required to fix the bug. "
        f"Return only the corrected code.\n\n{title}\n\n{code_text}"
    )


def _generic_code_warnings(code_text):
    warnings = []
    bracket_pairs = [("(", ")"), ("[", "]"), ("{", "}")]
    for left, right in bracket_pairs:
        if code_text.count(left) != code_text.count(right):
            warnings.append(f"Unbalanced {left}{right} delimiters.")

    quote_pairs = [("'", "single quotes"), ('"', 'double quotes')]
    for quote, label in quote_pairs:
        if code_text.count(quote) % 2 != 0:
            warnings.append(f"Unbalanced {label} detected.")

    if 'TODO' in code_text or 'FIXME' in code_text:
        warnings.append("TODO/FIXME markers remain in the code.")

    if '\t' in code_text:
        warnings.append("Tabs detected in source; consider normalizing indentation.")

    return warnings


def correct_with_gemini(text):
    if genai is None:
        print("Google GenAI library not available. Skipping Gemini correction.")
        return text

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    gemini_code_correction = os.environ.get("GEMINI_CODE_CORRECTION", "yes")
    gemini_model = os.environ.get("GEMINI_MODEL", "gemma-4-31b-it")
    gemini_fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
    thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", "8192"))

    if not gemini_api_key.strip():
        print("GEMINI_API_KEY not set. Skipping Gemini correction.")
        return text

    if gemini_code_correction.lower() != "yes":
        print("Gemini correction disabled.")
        return text

    try:
        client = genai.Client(api_key=gemini_api_key)
        cleaned_prompt = _build_gemini_prompt(
            "Correct the following OCR text/code for typos only. Do not change anything else.",
            text,
        )
        try:
            cleaned_text = _gemini_generate(client, gemini_model, cleaned_prompt, thinking_budget, max_attempts=15)
        except ThinkingBudgetUnsupported:
            print(f"Gemini model '{gemini_model}' does not support thinking budget. Switching to fallback model.")
            cleaned_text = _gemini_generate(client, gemini_fallback_model, cleaned_prompt, None, max_attempts=15)
        if not cleaned_text:
            print("Gemini returned no text. Trying fallback model.")
            cleaned_text = _gemini_generate(client, gemini_fallback_model, cleaned_prompt, None, max_attempts=15)
            if not cleaned_text:
                print("Gemini returned no text on fallback. Keeping OCR output.")
                return text

        correction_prompt = _build_gemini_prompt(
            "This OCR output is still incorrect in places. Make it functional and consistent with the original code. Only fix the actual bug-prone text; do not add commentary.",
            cleaned_text,
        )
        try:
            final_text = _gemini_generate(client, gemini_model, correction_prompt, thinking_budget, max_attempts=15)
        except ThinkingBudgetUnsupported:
            final_text = _gemini_generate(client, gemini_fallback_model, correction_prompt, None, max_attempts=15)
        if final_text:
            print("Gemini correction completed.")
            return final_text

        print("Primary model still failing, trying fallback model.")
        final_text = _gemini_generate(client, gemini_fallback_model, correction_prompt, None, max_attempts=15)
        if final_text:
            print("Gemini correction completed with fallback model.")
            return final_text

        print("Gemini correction completed.")
        return cleaned_text
    except Exception as e:
        print(f"Gemini correction failed: {e}")
        return text


def _prepare_ocr_image(image):
    if ImageOps is None or ImageFilter is None:
        return image

    working = image.convert("L")
    working = ImageOps.autocontrast(working)
    working = working.resize((working.width * 2, working.height * 2), resample=Image.Resampling.LANCZOS)
    working = working.filter(ImageFilter.SHARPEN)
    working = working.filter(ImageFilter.EDGE_ENHANCE_MORE)
    working = working.point(lambda pixel: 0 if pixel < 170 else 255, mode="1")
    return working


def _score_text(text):
    compact = " ".join(text.split())
    alnum = sum(1 for char in compact if char.isalnum())
    return (len(compact), alnum)


def _strong_ocr(image):
    configs = [
        "--oem 3 --psm 6",
        "--oem 3 --psm 4",
        "--oem 3 --psm 11",
    ]
    variants = [
        ("original", image),
        ("enhanced", _prepare_ocr_image(image)),
    ]

    best_text = ""
    best_score = (-1, -1)

    for variant_name, variant in variants:
        for config in configs:
            try:
                text = pytesseract.image_to_string(
                    variant,
                    lang="eng",
                    config=config,
                    timeout=45,
                )
                text = text.strip()
                if not text:
                    continue
                score = _score_text(text)
                print(f"OCR pass {variant_name} / {config}: {len(text)} chars")
                if score > best_score:
                    best_text = text
                    best_score = score
            except Exception as e:
                print(f"OCR pass {variant_name} / {config} failed: {e}")

    return best_text

if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    _tee = _TeeStream(sys.stdout)
    sys.stdout = _tee
    sys.stderr = _tee

    # List scanners and printers
    scanners = list_scanners_windows()
    device = None
    if scanners:
        print("Available scanners:")
        for i, s in enumerate(scanners, 1):
            print(f"  {i}. {s}")
        choice = input("Select scanner number (Enter for 1, 0 to skip scanning): ").strip()
        if choice == '0':
            print("Skipping scanning.")
        else:
            try:
                idx = int(choice) if choice else 1
                device = scanners[max(0, idx-1)]
                print(f"Selected scanner: {device}")
            except Exception:
                print("Invalid selection, using default.")

    printers, default_printer = list_printers_windows()
    selected_printer = None
    if printers:
        print("Available printers:")
        for i, p in enumerate(printers, 1):
            mark = ' (default)' if p == default_printer else ''
            print(f"  {i}. {p}{mark}")
        pchoice = input(f"Select printer number (Enter for default '{default_printer}', 0 to skip printing): ").strip()
        if pchoice == '0':
            print("Skipping printing.")
        else:
            try:
                if pchoice == '':
                    selected_printer = default_printer
                else:
                    pidx = int(pchoice)
                    selected_printer = printers[pidx-1]
                print(f"Selected printer: {selected_printer}")
            except Exception:
                print("Invalid printer selection; will use default.")
                selected_printer = default_printer

    # Start scanning
    print("Starting scan...")
    scanned_image = scan_to_highest_dpi(device=device)
    if not scanned_image:
        print("No scanned image produced. Exiting.")
        exit(1)

    print(f"Scanned image: {scanned_image}")

    # OCR
    if pytesseract is None or Image is None:
        print("pytesseract or PIL not available. Install them to perform OCR.")
        exit(1)

    print("Performing OCR...")
    _progress_simulator('OCR', seconds=2)
    try:
        img = Image.open(scanned_image)
        ocr_text = _strong_ocr(img)
    except Exception as e:
        print(f"OCR failed: {e}")
        ocr_text = ""

    # Optional Gemini correction
    corrected_text = ocr_text
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    if genai is not None and gemini_api_key.strip():
        try:
            client = genai.Client(api_key=gemini_api_key)
            prompt = (
                "Correct the following OCR Code for typos without changing structure or adding comments:\n\n" + ocr_text
            )
            response = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)
            if hasattr(response, 'text') and response.text:
                corrected_text = response.text
        except Exception as e:
            print(f"Gemini correction failed or unavailable: {e}")

    code_text = corrected_text or ocr_text

    include_report_page = os.environ.get('PRINT_REPORT_PAGE', '').lower() in {'1', 'true', 'yes', 'y'}
    if not include_report_page:
        try:
            include_report_page = input("Print a second page with warnings/output? [y/N]: ").strip().lower() in {'y', 'yes'}
        except Exception:
            include_report_page = False

    beautified_text = _beautify_code_text(code_text or '')
    syntax_lines = _syntax_status(beautified_text)
    generic_warnings = _generic_code_warnings(beautified_text)
    warnings = []
    if not ocr_text:
        warnings.append("OCR returned no text.")
    if pytesseract is None or Image is None:
        warnings.append("Missing pytesseract or PIL.")
    if genai is None:
        warnings.append("Google GenAI library not available.")
    if not os.environ.get('GEMINI_API_KEY', '').strip():
        warnings.append("GEMINI_API_KEY is not set.")
    if selected_printer is None:
        warnings.append("No explicit printer selected; system default or skip used.")
    if device is None and scanners:
        warnings.append("No scanner explicitly selected; default scanner was used.")
    warnings.extend(generic_warnings)

    detected_language = "unknown"
    try:
        detected_lexer = guess_lexer(beautified_text) if guess_lexer is not None and beautified_text else None
        detected_language = getattr(detected_lexer, "name", "unknown") if detected_lexer is not None else "unknown"
    except Exception:
        detected_language = "unknown"

    report_lines = [
        f"Scanner device: {device or 'default'}",
        f"Printer: {selected_printer or 'system default / skipped'}",
        f"Scan DPI: {max_dpi}",
        f"Output length: {len(beautified_text)} characters",
        "",
        "Warnings:",
        *(warnings or ["None"]),
        "",
        f"Gemini enabled: {'yes' if genai is not None and os.environ.get('GEMINI_API_KEY', '').strip() else 'no'}",
        f"Gemini model: {os.environ.get('GEMINI_MODEL', 'gemma-4-31b-it')}",
        f"Thinking budget: {os.environ.get('GEMINI_THINKING_BUDGET', '8192')}",
        f"Detected language: {detected_language}",
        *syntax_lines,
        "",
        "Output preview:",
        beautified_text[:1200] if beautified_text else '(no OCR output)',
        "",
        "Terminal log:",
        _tee.getvalue()[-8000:] if _tee.getvalue() else '(no terminal output captured)',
    ]

    # Generate PDF
    temp_pdf_file = os.path.join(tempfile.gettempdir(), "ocr_print_job.pdf")
    print("Generating styled PDF (if available)...")
    _progress_simulator('PDF', seconds=2)
    pdf_path = make_styled_pdf(beautified_text or '', report_lines=report_lines, include_report_page=include_report_page, out_pdf_path=temp_pdf_file)
    if pdf_path:
        print(f"PDF generated at {pdf_path}")
        temp_pdf_file = pdf_path
    else:
        print("Styled PDF generation failed.")
        temp_pdf_file = None

    # Print
    if temp_pdf_file and os.path.exists(temp_pdf_file):
        print("Sending to printer...")
        _progress_simulator('Printing', seconds=2)
        try:
            if win32print is not None and selected_printer:
                # Use win32print to set printer
                handle = win32print.OpenPrinter(selected_printer)
                try:
                    # Use ShellExecute as fallback for printing
                    if win32api is not None:
                        win32api.ShellExecute(0, "print", temp_pdf_file, None, ".", 0)
                    else:
                        os.startfile(temp_pdf_file, "print")
                finally:
                    win32print.ClosePrinter(handle)
            else:
                if win32api is not None:
                    win32api.ShellExecute(0, "print", temp_pdf_file, None, ".", 0)
                else:
                    try:
                        os.startfile(temp_pdf_file, "print")
                    except Exception:
                        print("Automatic printing not available on this system.")
            print("Print job submitted.")
        except Exception as e:
            print(f"Error while sending print job: {e}")
    else:
        print("No PDF to print.")

    # Save OCR text
    try:
        date_folder = os.path.dirname(scanned_image)
        base_name = os.path.basename(scanned_image)
        text_file = os.path.join(date_folder, base_name.rsplit('.', 1)[0] + '.txt')
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(beautified_text)
        print(f"OCR text written to {text_file}")
    except Exception as e:
        print(f"Failed to write OCR text file: {e}")