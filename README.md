# PaperCode

Scan paper, run OCR, optionally clean the text with Gemini, and print a readable code PDF on Linux or Windows.

## How to get the BEST results

- Write in lined paper and write straight.
    - Use 2 lines to write in (bigger = better OCR)
    - Use 1 blank lines before writing the next line
    - Do NOT write cursive
- Use Gemini Code Correction
- Test what OCR orientation works for your scanner (DISABLE GEMINI TEMPORARILY TO SAVE TOKENS) also skip printing and just look at the terminal output
    - There is no OCR orientation so rotating the paper is recommended.
- This is really optional but if you perfer a typewriter or something. You can use that (it will work better.)

## Requirements

Python packages:

```bash
pip install -r requirements.txt
```

System packages:

```bash
# Debian / Ubuntu
sudo apt update
sudo apt install -y tesseract-ocr sane-utils cups-client
```

Windows needs the scanner/printer drivers installed plus a TWAIN-compatible scanner and the Python `pywin32` package if you want native printer control.

## Environment Variables

Set these before running the script:

```bash
export GEMINI_API_KEY="your_api_key_here"
export GEMINI_MODEL="gemini-2.5-pro"
export GEMINI_THINKING_BUDGET="8192"
export GEMINI_CODE_CORRECTION="yes"
export PRINT_REPORT_PAGE="yes"
```

`GEMINI_CODE_CORRECTION` can be set to `no` if you only want the OCR cleanup pass.

## Running

Use the launcher so the right OS script is selected:

```bash
python3 checkforos.py
```

Or run the platform script directly:

```bash
python3 linux_script.py
python3 win_script.py
```

## First Time
When launching for the first time, you must select the scanner, printer and the image orientation.
After that it selects that automatically but you can change it by pressing enter in 7 seconds when said so.
'There might be a few extra scanners/printers experiment and check what is the right one.'

## What It Does

- Detects available scanners and printers.
- Scans at the highest resolution it can find.
- Runs multi-pass OCR with image cleanup.
- Optionally sends the OCR text through Gemini with a thinking budget.
- Beautifies the code before PDF generation.
- Prints a clean white code page with black default text and syntax colors.
- Optionally adds a second report page with warnings, syntax status, and an output preview.

## Common Errors

### `The tesseract binary is not installed`

Install the system OCR engine:

```bash
sudo apt install -y tesseract-ocr
```

### `Google GenAI library not available`

Install the Python package and make sure you are running the project venv:

```bash
python3 -m pip install -r requirements.txt
```

If needed, run:

```bash
/home/jos/papercode/.venv/bin/python checkforos.py
```

### No scanners found

- Check the USB cable and power.
- Run `scanimage -L` on Linux.
- Install the scanner driver or TWAIN driver on Windows.

### No printers found

- Check `lpstat -p` on Linux.
- Make sure CUPS is installed and the printer is configured.
- On Windows, confirm the printer appears in Devices and Printers.

### OCR output is still poor

- Confirm the page is flat and well lit.
- Try a higher-resolution scan.
- Make sure the scanner glass is clean.
- If the code is skewed, rescan it with straighter alignment.
- Check [How to get the BEST results](#how-to-get-the-best-results)

## Notes

- Linux uses the project `.venv` automatically when possible.
- Gemini is optional. If no API key is set, the script still runs.# papercode
Set Default printer as your target printer.

Start the python script