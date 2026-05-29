import os
import subprocess
import time
import shutil
import sys
import glob
import ast
import re
import json
import select
from datetime import datetime
from pathlib import Path


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


def _bootstrap_local_venv_site_packages():
    script_dir = Path(__file__).resolve().parent
    venv_root = script_dir / ".venv"
    if not venv_root.exists():
        return

    if sys.prefix == str(venv_root):
        return

    candidates = []
    for pattern in (
        venv_root / "lib" / "python*" / "site-packages",
        venv_root / "Lib" / "site-packages",
    ):
        candidates.extend(glob.glob(str(pattern)))

    for candidate in candidates:
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


_bootstrap_local_venv_site_packages()

try:
    from PIL import Image, ImageOps, ImageFilter
except Exception:
    Image = None
    ImageOps = None
    ImageFilter = None

try:
    import tkinter as tk
except Exception:
    tk = None

try:
    from PIL import ImageTk
except Exception:
    ImageTk = None

try:
    import pytesseract
except Exception:
    pytesseract = None

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
    from pygments.lexers import guess_lexer, get_lexer_by_name
    from pygments.token import Keyword, Name, Comment, String, Number, Operator
except Exception:
    guess_lexer = None

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, XPreformatted, PageBreak, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
except Exception:
    A4 = None

try:
    from tqdm import tqdm
except Exception:
    tqdm = None


THINKING_UNSUPPORTED_MODELS = set()
PREFERENCES_PATH = Path(__file__).resolve().with_name(".scanner_printer_prefs.json")


class ThinkingBudgetUnsupported(Exception):
    pass


def _load_device_preferences():
    try:
        with open(PREFERENCES_PATH, 'r', encoding='utf-8') as file_handle:
            data = json.load(file_handle)
        if isinstance(data, dict):
            return {
                'scanner': data.get('scanner', ''),
                'printer': data.get('printer', ''),
                'ocr_orientation': data.get('ocr_orientation', None),
            }
    except Exception:
        pass
    return {'scanner': '', 'printer': '', 'ocr_orientation': None}


def _save_device_preferences(scanner_name=None, printer_name=None, ocr_orientation=None):
    current = _load_device_preferences()
    if scanner_name is not None:
        current['scanner'] = scanner_name or ''
    if printer_name is not None:
        current['printer'] = printer_name or ''
    if ocr_orientation is not None:
        current['ocr_orientation'] = ocr_orientation

    try:
        with open(PREFERENCES_PATH, 'w', encoding='utf-8') as file_handle:
            json.dump(current, file_handle, indent=2, sort_keys=True)
    except Exception as e:
        print(f"Failed to save device preferences: {e}")


def _read_enter_with_timeout(prompt, timeout_seconds=7):
    print(prompt, end='', flush=True)
    if not sys.stdin.isatty():
        print()
        return None

    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
        if not ready:
            print()
            return None
        line = sys.stdin.readline()
        print()
        return line.strip()
    except Exception:
        print()
        return None


def _extract_scanner_device(selected_line):
    dev = None
    if '`' in selected_line:
        dev = selected_line.split('`', 2)[1]
    elif "'" in selected_line:
        parts = selected_line.split("'")
        if len(parts) >= 2:
            dev = parts[1]
    elif 'device ' in selected_line:
        dev = selected_line.split('device ', 1)[1].split()[0]
    return dev


def _pick_scanner(scanners, saved_scanner=''):
    if not scanners:
        return None

    if saved_scanner and any(saved_scanner in line for line in scanners):
        response = _read_enter_with_timeout(
            f"Saved scanner: {saved_scanner} [Enter to reselect, wait 7s to use saved]: ",
            timeout_seconds=7,
        )
        if response is None:
            print(f"Using saved scanner: {saved_scanner}")
            return saved_scanner

    print("Available scanners:")
    for i, scanner in enumerate(scanners, 1):
        print(f"  {i}. {scanner}")
    choice = input("Select scanner number (Enter for 1, 0 to skip scanning): ").strip()
    if choice == '0':
        print("Skipping scanning.")
        return None

    try:
        index = int(choice) if choice else 1
        selected = scanners[max(0, index - 1)]
        device_name = _extract_scanner_device(selected)
        print(f"Selected device: {device_name}")
        return device_name
    except Exception:
        print("Invalid selection, using default device.")
        return _extract_scanner_device(scanners[0])


def _pick_printer(printers, default_printer='', saved_printer=''):
    if not printers:
        return None

    if saved_printer and saved_printer in printers:
        response = _read_enter_with_timeout(
            f"Saved printer: {saved_printer} [Enter to reselect, wait 7s to use saved]: ",
            timeout_seconds=7,
        )
        if response is None:
            print(f"Using saved printer: {saved_printer}")
            return saved_printer

    print("Available printers:")
    for i, printer in enumerate(printers, 1):
        mark = ' (default)' if printer == default_printer else ''
        print(f"  {i}. {printer}{mark}")
    pchoice = input(f"Select printer number (Enter for default '{default_printer}', 0 to skip printing): ").strip()
    if pchoice == '0':
        print("Skipping printing.")
        return None

    try:
        if pchoice == '':
            selected_printer = default_printer
        else:
            pidx = int(pchoice)
            selected_printer = printers[pidx - 1]
        print(f"Selected printer: {selected_printer}")
        return selected_printer
    except Exception:
        print("Invalid printer selection; printing will use system default.")
        return default_printer or None


def _orientation_spec_label(orientation_spec):
    if not orientation_spec:
        return "rotation 0°, no flip"
    angle = int(orientation_spec.get('angle', 0)) % 360
    flipped = bool(orientation_spec.get('flipped', False))
    return f"rotation {angle}°{' + flipped' if flipped else ''}"


def _apply_ocr_orientation(image, orientation_spec):
    if not orientation_spec:
        return image

    angle = int(orientation_spec.get('angle', 0)) % 360
    flipped = bool(orientation_spec.get('flipped', False))

    working = image
    if angle:
        working = working.rotate(-angle, expand=True)

    if flipped and Image is not None:
        transpose_method = getattr(Image, 'Transpose', None)
        if transpose_method is not None and hasattr(transpose_method, 'FLIP_LEFT_RIGHT'):
            working = working.transpose(transpose_method.FLIP_LEFT_RIGHT)
        else:
            working = working.transpose(Image.FLIP_LEFT_RIGHT)

    return working


def _build_ocr_orientation_candidates():
    candidates = []
    for angle in (0, 90, 180, 240):
        candidates.append({'angle': angle, 'flipped': False})
    for angle in (0, 90, 180, 240):
        candidates.append({'angle': angle, 'flipped': True})
    return candidates


def _show_ocr_orientation_popup(image, orientation_spec):
    if tk is None or ImageTk is None:
        return None

    try:
        root = tk.Tk()
    except Exception as e:
        print(f"OCR orientation popup unavailable: {e}")
        return None

    root.title("Confirm OCR orientation")
    root.geometry("900x900")
    root.minsize(640, 520)

    result = {'accepted': None}
    photo_holder = {'photo': None}

    preview = image.copy()
    preview.thumbnail((820, 700), resample=Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(preview)
    photo_holder['photo'] = photo

    info = tk.Label(
        root,
        text=f"Is this the correct OCR orientation?\n{_orientation_spec_label(orientation_spec)}\nPress Yes / y or No / n",
        font=("Sans", 13),
        justify="center",
        pady=12,
    )
    info.pack(side="top", fill="x")

    image_label = tk.Label(root, image=photo)
    image_label.pack(side="top", expand=True, fill="both", padx=12, pady=12)

    button_frame = tk.Frame(root)
    button_frame.pack(side="bottom", pady=18)

    def accept():
        result['accepted'] = True
        root.destroy()

    def reject():
        result['accepted'] = False
        root.destroy()

    yes_button = tk.Button(button_frame, text="Yes", width=14, command=accept)
    no_button = tk.Button(button_frame, text="No", width=14, command=reject)
    yes_button.pack(side="left", padx=12)
    no_button.pack(side="left", padx=12)

    root.bind('<y>', lambda event: accept())
    root.bind('<Y>', lambda event: accept())
    root.bind('<Return>', lambda event: accept())
    root.bind('<n>', lambda event: reject())
    root.bind('<N>', lambda event: reject())
    root.bind('<Escape>', lambda event: reject())
    root.protocol("WM_DELETE_WINDOW", reject)
    root.mainloop()
    return result['accepted']


def _pick_ocr_orientation(image, saved_orientation=None):
    if Image is None:
        return image, None

    if saved_orientation:
        response = _read_enter_with_timeout(
            f"Saved OCR orientation: {_orientation_spec_label(saved_orientation)} [Enter to reselect, wait 7s to use saved]: ",
            timeout_seconds=7,
        )
        if response is None:
            print(f"Using saved OCR orientation: {_orientation_spec_label(saved_orientation)}")
            return _apply_ocr_orientation(image, saved_orientation), saved_orientation

    candidates = _build_ocr_orientation_candidates()
    for orientation_spec in candidates:
        preview_image = _apply_ocr_orientation(image, orientation_spec)
        accepted = _show_ocr_orientation_popup(preview_image, orientation_spec)
        if accepted is None:
            break
        if accepted:
            print(f"Selected OCR orientation: {_orientation_spec_label(orientation_spec)}")
            return preview_image, orientation_spec

    fallback_spec = {'angle': 0, 'flipped': False}
    print("No OCR orientation selected; using rotation 0°, no flip.")
    return _apply_ocr_orientation(image, fallback_spec), fallback_spec


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


def list_scanners_linux():
    # Uses `scanimage -L` to list sane devices
    if shutil.which('scanimage') is None:
        return []
    try:
        proc = subprocess.run(['scanimage', '-L'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        out = proc.stdout.decode(errors='ignore')
        devices = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                # Example: "device `epson2:libusb:002:003' is a Epson ..."
                devices.append(line)
        return devices
    except Exception:
        return []


def list_printers_linux():
    # Uses `lpstat -p` and `lpstat -d` to list printers
    printers = []
    if shutil.which('lpstat') is None:
        return printers
    try:
        proc = subprocess.run(['lpstat', '-p'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        out = proc.stdout.decode(errors='ignore')
        for line in out.splitlines():
            if line.startswith('printer '):
                parts = line.split()
                if len(parts) >= 2:
                    printers.append(parts[1])
        # try default
        proc2 = subprocess.run(['lpstat', '-d'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        default = None
        if proc2.returncode == 0:
            text = proc2.stdout.decode(errors='ignore')
            if 'system default destination:' in text:
                default = text.split(':', 1)[1].strip()
        return printers, default
    except Exception:
        return [], None


def detect_max_scan_resolution(device=None):
    if shutil.which('scanimage') is None:
        return None

    scan_cmd = ['scanimage', '--help']
    if device:
        scan_cmd += ['-d', device]

    try:
        proc = subprocess.run(scan_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        output = proc.stdout.decode(errors='ignore') + proc.stderr.decode(errors='ignore')
        values = [int(match) for match in re.findall(r'(?<!\d)(\d{2,4})\s*dpi\b', output, flags=re.IGNORECASE)]
        if values:
            return max(values)
    except Exception:
        pass

    return None


def scan_to_highest_dpi(base_path=None, preferred_dpi=None, device=None):
    if base_path is None:
        base_path = os.path.expanduser("~/Scans")

    detected_dpi = detect_max_scan_resolution(device)
    if preferred_dpi is None:
        preferred_dpi = detected_dpi or 1200

    if detected_dpi and detected_dpi > preferred_dpi:
        preferred_dpi = detected_dpi

    now = datetime.now()
    date_folder = os.path.join(base_path, now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"))
    os.makedirs(date_folder, exist_ok=True)

    file_name = now.strftime("%H-%M-%S.png")
    scanned_image = os.path.join(date_folder, file_name)

    print(f"Preparing to scan to: {scanned_image}")
    print(f"Using scan resolution: {preferred_dpi} DPI")
    scan_to_highest_dpi.last_dpi = preferred_dpi
    scan_to_highest_dpi.last_device = device

    # Use `scanimage` from sane-utils on Linux. Write output to the file.
    if device:
        scan_cmd = ["scanimage", "-d", device, "--format=png", "--mode", "Color", "--resolution", str(preferred_dpi)]
    else:
        scan_cmd = ["scanimage", "--format=png", "--mode", "Color", "--resolution", str(preferred_dpi)]

    try:
        with open(scanned_image, "wb") as out_file:
            proc = subprocess.Popen(scan_cmd, stdout=out_file, stderr=subprocess.PIPE)
            print("Scanning", end='', flush=True)
            while proc.poll() is None:
                print('.', end='', flush=True)
                time.sleep(0.5)
            ret = proc.wait()
            if ret == 0:
                print(f"\nImage saved to {scanned_image}")
                return scanned_image
            else:
                stderr = proc.stderr.read().decode(errors='ignore') if proc.stderr is not None else ''
                print(f"\nScan failed, stderr: {stderr}")
                return None
    except FileNotFoundError:
        print("`scanimage` not found. Please install sane-utils and ensure a scanner is connected.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Scan failed at {preferred_dpi} DPI, stderr: {e.stderr.decode(errors='ignore')}")
        # Try a safe fallback dpi
        try:
            fallback_dpi = min(600, preferred_dpi) if preferred_dpi else 600
            print(f"Retrying with {fallback_dpi} DPI...")
            scan_cmd[-1] = str(fallback_dpi)
            with open(scanned_image, "wb") as out_file:
                subprocess.run(scan_cmd, stdout=out_file, stderr=subprocess.PIPE, check=True)
            print(f"Image saved to {scanned_image}")
            return scanned_image
        except Exception as e2:
            print(f"Fallback scan failed: {e2}")
            return None


def ocr_image(image_path, saved_orientation=None):
    if pytesseract is None or Image is None:
        print("pytesseract or PIL not available. Install pillow and pytesseract.")
        return None, saved_orientation

    if shutil.which('tesseract') is None:
        print("The `tesseract` binary is not installed. Install the `tesseract-ocr` system package on Linux.")
        return None

    try:
        img = Image.open(image_path)
    except Exception as e:
        print(f"Could not open image: {e}")
        return None, saved_orientation

    try:
        img, selected_orientation = _pick_ocr_orientation(img, saved_orientation)
    except Exception as e:
        print(f"OCR orientation selection failed, using original image: {e}")
        selected_orientation = saved_orientation

    try:
        return _strong_ocr(img), selected_orientation
    except Exception as e:
        print(f"OCR failed: {e}")
        return None, selected_orientation


def _beautify_code_text(code_text):
    cleaned = (code_text or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    try:
        lexer = guess_lexer(cleaned) if guess_lexer is not None else None
    except Exception:
        lexer = None

    lexer_name = getattr(lexer, "name", "").lower() if lexer is not None else ""

    if lexer_name in {"html", "xml", "xhtml"}:
        cleaned = _normalize_markup_spacing(cleaned)

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


def _normalize_markup_spacing(text):
    def _normalize_tag(match):
        inner = re.sub(r"\s+", " ", match.group(1).strip())
        inner = re.sub(r"^/\s+", "/", inner)
        return f"<{inner}>"

    normalized = re.sub(r"<\s*([^<>]+?)\s*>", _normalize_tag, text)
    normalized = re.sub(r"</\s*([A-Za-z0-9:_-]+)\s*>", r"</\1>", normalized)
    return normalized


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
                or ('thinking budget' in error_text.lower() and 'unsupported' in error_text.lower())
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


def _build_gemini_prompt(title, code_text, extra_instructions=None):
    instructions = [
        "You are an expert programmer.",
        "Your ONLY task is to repair the code below.",
        "Fix only syntax errors, logical bugs, OCR mistakes, whitespace corruption, broken indentation, and crashes that prevent it from working as intended.",
        "Reconstruct missing indentation and spacing so the output is properly aligned for display, printing, PDF export, and saved output.",
        "Do not refactor, do not rename variables, do not add explanations, do not add comments, and do not change the overall structure unless required to fix the bug.",
    ]
    if extra_instructions:
        instructions.append(extra_instructions)
    instructions.append("Return only the corrected code.")
    instructions.append(title)
    instructions.append(code_text)
    return (
        "\n\n".join(instructions)
    )


def _strip_save_directives(text):
    cleaned_lines = []
    requested_name = None
    directive_pattern = re.compile(r"^\s*#{3}\s*save\s+as\s+(.+?)\s*$", re.IGNORECASE)

    for line in (text or "").splitlines():
        match = directive_pattern.match(line)
        if match:
            if requested_name is None:
                requested_name = match.group(1).strip()
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip(), requested_name


def _language_extension_hint(lexer_name):
    lexer_name = (lexer_name or "").lower()
    mapping = [
        ("html", ".html"),
        ("xml", ".xml"),
        ("xhtml", ".html"),
        ("javascript", ".js"),
        ("jsx", ".jsx"),
        ("typescript", ".ts"),
        ("tsx", ".tsx"),
        ("json", ".json"),
        ("css", ".css"),
        ("python", ".py"),
        ("c++", ".cpp"),
        ("cpp", ".cpp"),
        ("c#", ".cs"),
        ("csharp", ".cs"),
        ("c", ".c"),
        ("java", ".java"),
        ("go", ".go"),
        ("golang", ".go"),
        ("php", ".php"),
        ("ruby", ".rb"),
        ("shell", ".sh"),
        ("bash", ".sh"),
        ("yaml", ".yml"),
        ("markdown", ".md"),
        ("perl", ".pl"),
        ("rust", ".rs"),
        ("swift", ".swift"),
        ("kotlin", ".kt"),
        ("dart", ".dart"),
        ("lua", ".lua"),
    ]
    for needle, extension in mapping:
        if needle in lexer_name:
            return extension
    return ".txt"


def _sanitize_output_filename(candidate, fallback_extension=".txt", default_stem="exportedcode"):
    if not candidate:
        return None

    candidate = candidate.strip().strip('"').strip("'").strip("`")
    candidate = os.path.basename(candidate)
    candidate = candidate.replace("custom", "").strip()
    if not candidate:
        return None

    stem, extension = os.path.splitext(candidate)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    if not stem:
        stem = default_stem

    if not extension:
        extension = fallback_extension or ".txt"
    if not extension.startswith("."):
        extension = f".{extension}"
    extension = re.sub(r"[^A-Za-z0-9.]+", "", extension) or ".txt"

    return f"{stem}{extension}"


def _parse_filename_response(response_text, fallback_extension=".txt", default_stem="exportedcode"):
    if not response_text:
        return None, False

    cleaned_lines = []
    saw_custom = False
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        if line.lower() == "custom":
            saw_custom = True
            continue
        cleaned_lines.append(line)

    if not cleaned_lines:
        return None, saw_custom

    return _sanitize_output_filename(cleaned_lines[0], fallback_extension, default_stem), saw_custom


def _suggest_output_filename(client, primary_model, fallback_model, code_text, detected_language, explicit_filename=None):
    fallback_extension = _language_extension_hint(detected_language)
    if explicit_filename:
        explicit_name = _sanitize_output_filename(explicit_filename, fallback_extension) or _sanitize_output_filename("exportedcode.txt", fallback_extension)
        return explicit_name, True, None

    prompt = (
        "Return exactly one filename with extension and nothing else.\n"
        "No path, no markdown, no explanation, no code fences.\n"
        "If a custom save directive is present, output the word custom on its own line first and then the filename on the next line.\n"
        f"The filename must match this code's language and purpose. Preferred extension: {fallback_extension}\n"
        f"Detected language: {detected_language or 'unknown'}\n\n"
        f"Code:\n{code_text[:8000]}"
    )

    for model_name in (primary_model, fallback_model):
        if not model_name:
            continue
        try:
            response_text = _gemini_generate(client, model_name, prompt, None, max_attempts=5)
        except Exception as exc:
            print(f"Filename suggestion failed on {model_name}: {exc}")
            continue

        parsed_name, saw_custom = _parse_filename_response(response_text, fallback_extension)
        if parsed_name:
            return parsed_name, saw_custom, model_name

    timestamp_name = datetime.now().strftime("%Y%m%d_%H%M%S_exportedcode.txt")
    return timestamp_name, False, None


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
        cleaned_source, requested_filename = _strip_save_directives(text)
        cleaned_prompt = _build_gemini_prompt(
            "Correct the following OCR text/code for typos only. Do not change anything else.",
            cleaned_source or text,
            extra_instructions=(
                "Preserve and restore indentation, spacing, and alignment exactly where needed so the result prints cleanly and reads correctly in a PDF. "
                "If the OCR text contains a filename hint, respect it in the filename-generation step, not in the code output. "
                "Do not emit save directives or custom markers in the corrected code."
            ),
        )
        cleaned_text = None
        for attempt in range(1, 4):
            try:
                cleaned_text = _gemini_generate(client, gemini_model, cleaned_prompt, thinking_budget, max_attempts=15)
                break
            except ThinkingBudgetUnsupported:
                if attempt < 3:
                    print(f"Gemini model '{gemini_model}' does not support thinking budget. Retrying without it ({attempt}/3)...")
                else:
                    print(f"Gemini model '{gemini_model}' does not support thinking budget after 3 tries. Switching to fallback model.")
                thinking_budget = None

        if not cleaned_text:
            print("Gemini returned no text. Trying fallback model.")
            cleaned_text = _gemini_generate(client, gemini_fallback_model, cleaned_prompt, None, max_attempts=15)
            if not cleaned_text:
                print("Gemini returned no text on fallback. Keeping OCR output.")
                return text

        if gemini_code_correction.lower() == "yes":
            correction_prompt = _build_gemini_prompt(
                "This OCR output is still incorrect in places. Make it functional and consistent with the original code. Only fix the actual bug-prone text; do not add commentary.",
                cleaned_text,
                extra_instructions=(
                    "Keep the whitespace and indentation structure readable and aligned for source code, HTML, and printed output. "
                    "Do not introduce filename directives, notes, or custom markers."
                ),
            )
            final_text = None
            retry_thinking_budget = thinking_budget
            for attempt in range(1, 4):
                try:
                    final_text = _gemini_generate(client, gemini_model, correction_prompt, retry_thinking_budget, max_attempts=15)
                    break
                except ThinkingBudgetUnsupported:
                    if attempt < 3:
                        print(f"Gemini correction model '{gemini_model}' does not support thinking budget. Retrying without it ({attempt}/3)...")
                    else:
                        print(f"Gemini correction model '{gemini_model}' does not support thinking budget after 3 tries. Switching to fallback model.")
                    retry_thinking_budget = None
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


def make_styled_pdf(code_text, report_lines=None, include_report_page=False, out_pdf_path="/tmp/ocr_print_job.pdf"):
    if guess_lexer is None or A4 is None:
        print("Missing dependencies for PDF generation (pygments/reportlab). Skipping PDF creation.")
        return None

    formatted_code = _colorize_code(code_text)

    doc = SimpleDocTemplate(out_pdf_path, pagesize=A4, leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28)
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


def print_pdf(pdf_path, printer=None):
    if not os.path.exists(pdf_path):
        print(f"PDF not found: {pdf_path}")
        return False
    # Use CUPS/lp to print on Linux
    lp = shutil.which('lp') or shutil.which('lpr')
    if lp is None:
        print("No print command found (lp/lpr). Install cups-client.")
        return False
    try:
        cmd = [lp]
        if os.path.basename(lp) == 'lp' and printer:
            cmd += ['-d', printer, '-o', 'media=A4', '-o', 'fit-to-page', '-o', 'print-quality=5']
        elif os.path.basename(lp) == 'lp':
            cmd += ['-o', 'media=A4', '-o', 'fit-to-page', '-o', 'print-quality=5']
        cmd += [pdf_path]
        subprocess.run(cmd, check=True)
        print("Print job submitted.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Printing failed: {e}")
        return False


if __name__ == "__main__":
    os.system('clear')
    _tee = _TeeStream(sys.stdout)
    sys.stdout = _tee
    sys.stderr = _tee

    saved_preferences = _load_device_preferences()

    # Show available scanners
    scanners = list_scanners_linux()
    device_name = _pick_scanner(scanners, saved_preferences.get('scanner', '')) if scanners else None
    if device_name:
        _save_device_preferences(scanner_name=device_name)

    else:
        print("No scanners detected (scanimage -L missing or no device).")

    # Show available printers
    printers, default_printer = list_printers_linux()
    selected_printer = _pick_printer(printers, default_printer, saved_preferences.get('printer', '')) if printers else None
    if selected_printer:
        _save_device_preferences(printer_name=selected_printer)
    else:
        print("No printers detected (lpstat missing or no printers configured).")

    include_report_page = os.environ.get('PRINT_REPORT_PAGE', '').lower() in {'1', 'true', 'yes', 'y'}
    if not include_report_page:
        try:
            include_report_page = input("Print a second page with warnings/output? [y/N]: ").strip().lower() in {'y', 'yes'}
        except Exception:
            include_report_page = False

    # Start scan (if device selected)
    if device_name:
        print("Starting scan...")
        scanned_image = scan_to_highest_dpi(device=device_name)
    else:
        scanned_image = scan_to_highest_dpi()

    if not scanned_image:
        print("No scanned image produced. Exiting.")
        exit(1)

    print(f"Scanned image at: {scanned_image}")

    # OCR with progress
    print("Performing OCR...")
    _progress_simulator('OCR', seconds=2)
    ocr_text, selected_ocr_orientation = ocr_image(scanned_image, saved_preferences.get('ocr_orientation'))
    if selected_ocr_orientation is not None:
        _save_device_preferences(ocr_orientation=selected_ocr_orientation)
    if not ocr_text:
        print("OCR produced no text.")
    else:
        print("OCR completed (first 200 chars):")
        print(ocr_text[:200].strip())

    cleaned_ocr_text, requested_filename = _strip_save_directives(ocr_text or "")
    if requested_filename:
        print(f"Save-as directive detected: {requested_filename}")

    # Optional Gemini correction
    print("Running Gemini correction (if enabled)...")
    _progress_simulator('Gemini', seconds=2)
    code_text = correct_with_gemini(cleaned_ocr_text or "")

    beautified_text = _beautify_code_text(code_text or "")
    beautified_text, _ = _strip_save_directives(beautified_text)
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
    if device_name is None and scanners:
        warnings.append("No scanner explicitly selected; default scanner was used.")
    warnings.extend(generic_warnings)

    detected_language = "unknown"
    try:
        detected_lexer = guess_lexer(beautified_text) if guess_lexer is not None and beautified_text else None
        detected_language = getattr(detected_lexer, "name", "unknown") if detected_lexer is not None else "unknown"
    except Exception:
        detected_language = "unknown"

    auto_filename_enabled = os.environ.get('GEMINI_AUTO_FILENAME', 'yes').lower() in {'1', 'true', 'yes', 'y'}
    export_filename = None
    export_filename_source = "timestamp"
    export_custom_marker = False

    if auto_filename_enabled:
        export_filename = None
        if genai is not None and os.environ.get('GEMINI_API_KEY', '').strip():
            try:
                client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY', '').strip())
                export_filename, export_custom_marker, filename_model = _suggest_output_filename(
                    client,
                    os.environ.get('GEMINI_MODEL', 'gemma-4-31b-it'),
                    os.environ.get('GEMINI_FALLBACK_MODEL', 'gemini-flash-lite-latest'),
                    beautified_text or code_text or ocr_text or '',
                    detected_language,
                    explicit_filename=requested_filename,
                )
                if export_filename:
                    export_filename_source = "custom" if export_custom_marker else (filename_model or "gemini")
            except Exception as e:
                print(f"Filename generation failed, falling back to timestamp name: {e}")

    if not export_filename:
        export_filename = datetime.now().strftime("%Y%m%d_%H%M%S_exportedcode.txt")
        export_filename_source = "timestamp"

    export_stem, export_extension = os.path.splitext(export_filename)
    if not export_stem:
        export_stem = datetime.now().strftime("%Y%m%d_%H%M%S_exportedcode")
    if not export_extension:
        export_extension = ".txt"

    export_path = os.path.join(os.path.dirname(scanned_image), export_filename)
    pdf_path = os.path.join(os.path.dirname(scanned_image), f"{export_stem}.pdf")

    gemini_summary = [
        f"Gemini enabled: {'yes' if genai is not None and os.environ.get('GEMINI_API_KEY', '').strip() else 'no'}",
        f"Gemini model: {os.environ.get('GEMINI_MODEL', 'gemma-4-31b-it')}",
        f"Thinking budget: {os.environ.get('GEMINI_THINKING_BUDGET', '8192')}",
        f"Detected language: {detected_language}",
        f"Export filename: {export_filename}",
        f"Filename source: {export_filename_source}",
    ]
    terminal_log = _tee.getvalue()
    report_lines = [
        f"Scanner device: {device_name or 'default'}",
        f"Printer: {selected_printer or 'system default / skipped'}",
        f"Scanned image: {scanned_image}",
        f"Export file: {export_path}",
        f"PDF file: {pdf_path}",
        f"Resolved scan DPI: {detect_max_scan_resolution(device_name) or 'unknown'}",
        f"Output length: {len(beautified_text)} characters",
        "",
        "Warnings:",
        *(warnings or ["None"]),
        *gemini_summary,
        *syntax_lines,
        "",
        "Output preview:",
        beautified_text[:1200] if beautified_text else '(no OCR output)',
        "",
        "Terminal log:",
        terminal_log[-8000:] if terminal_log else '(no terminal output captured)',
    ]

    # PDF generation
    print("Generating styled PDF (if available)...")
    _progress_simulator('PDF', seconds=2)
    pdf_path = make_styled_pdf(beautified_text or '', report_lines=report_lines, include_report_page=include_report_page, out_pdf_path=pdf_path)
    if pdf_path:
        print(f"PDF created: {pdf_path}")
        # Print if requested
        if selected_printer:
            print("Sending to printer...")
            _progress_simulator('Printing', seconds=2)
            print_pdf(pdf_path, printer=selected_printer)
        else:
            print("Printer not selected or unavailable; skipping print.")
    else:
        print("PDF generation skipped or failed.")

    # Save exported code
    try:
        with open(export_path, 'w', encoding='utf-8') as f:
            f.write(beautified_text or '')
        print(f"Exported code written to {export_path}")
    except Exception as e:
        print(f"Failed to write exported code file: {e}")
