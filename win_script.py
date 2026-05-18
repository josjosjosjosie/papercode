import pytesseract
import os
import twain
import subprocess
from datetime import datetime
from google import genai
from PIL import Image

def scan_to_highest_dpi():
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

    # 4. Connect to the Source Manager
    # 0 indicates the parent window handle (none in this headless case)
    sm = twain.SourceManager(0)
    sources = sm.source_list
    
    if not sources:
        print("No scanners found. Please check your connection or drivers.")
        return None

    # Automatically pick the first available scanner (default)
    default_scanner = sources[0]
    print(f"Using scanner: {default_scanner}")

    # 5. Find the highest supported DPI
    max_dpi = 300 # Fallback default in case the scanner doesn't report capabilities properly
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

    print(f"Scanning at {max_dpi} DPI... Please wait.")

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

if __name__ == "__main__":
    # Execute the function and store the result in the requested variable
    scanned_image = scan_to_highest_dpi()
    
    # You can now use the 'scanned_image' variable elsewhere in your code
    if scanned_image:
        print(f"Variable 'scanned_image' currently holds: {scanned_image}")

    

text = pytesseract.image_to_string(image, lang=None, config='', nice=0, timeout=0)

# Set the tesseract path if it's not in your system environment variables
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Load the image from your path
image_path = "path_to_your_image.png"
img = Image.open(image_path)

# Perform OCR and store the extracted text directly into the requested variable
scanned_image = pytesseract.image_to_string(
    image=img, 
    lang=None, 
    config='', 
    nice=0, 
    timeout=0
)

# _____________________________________________________________________________________________
# GEMINI OCR text typo correction and optional code fixing
gemini_api_key = "YOUR_API_KEY_HERE" # Replace with your actual API key

client = genai.Client()

# Prompt the model to correct typos in the OCR text while preserving the code structure and functionality 
response = client.models.generate_content(
    model="gemini-2.5-flash-lite",
    # Gemini 2.5 Flash Lite is cheap and fast, but good enough to fix typos in code. You can experiment with other models if you want, but this one should be sufficient for most OCR typo corrections.
    contents="Correct the following OCR Code (this could be any programming language) for typos. DO NOT EDIT ANYTHING ELSE AND DO NOT CHANGE CODE ONLY FIX TYPING ERRORS THAT MAKES THE CODE NOT FUNCTIONABLE DO NOT SAY ANYTHING ELSE JUST ANWSER WITH CODE DONT ADD YOUR OWN COMMENTS OR ANYTHING ELSE" + scanned_image,
)
# The corrected code is now stored in the 'response.text' variable, which you can use for further processing or printing.

# CODE CORRECTION SETTING
gemini_code_correction = "Yes" # Set to "Yes" if you want Gemini to attempt to fix any code errors it detects in the OCR text, or "No" if you only want it to correct typos without changing any code structure. This is useful if you want to preserve the original code as much as possible while still fixing obvious typos that would prevent it from running. Set to "No" If you do not want any code changes at all and only want to fix typos, but be aware that if the OCR text has significant errors that prevent it from running, you might want to set this to "Yes" to allow Gemini to make necessary corrections to get the code working.

# Choose a model (Gemini 3 Flash is good enough to make some code corrections, For bigger or more complex code a Pro model is recommended.)
if (gemini_code_correction == "Yes"): 
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents='You are an expert programmer. I will provide a piece of code below that is currently not working (correctly). Your ONLY task is to make this code functional. You must strictly adhere to the following rules: 1. FIX ONLY: Only repair the syntax errors, logical bugs, or crashes that prevent the code from working as intended. 2. NO REFACTORING: Do not alter the structure of the code. Do not rename variables, do not rewrite loops into list comprehensions, and do not change the overall architecture. 3. NO SHORTENING: Do not make the code shorter, more compact, or "cleaner" if the current setup can be made functional as it is. 4. DO NOT ADD ANYTHING: Do not add new features, extra validations, comments, or functions, UNLESS it is absolutely necessary to fix the specific bug. 5. PRESERVE THE STYLE: Write the fixes in the exact same coding style as the rest of the provided code. 6. ABSOLUTELY NO COMMENTAIRY AND DO NOT ADD YOUR OWN MESSAGE PROVIDE ONLY CODE. Provide the full, corrected code without any extra explanation, unless I explicitly ask for it. | Here is the code that needs to be fixed:' + response.text,)

    print(responese.text)
elif (gemini_code_correction == "No"):
    print("Skipped code correction.")

#______________________________________________________________________________________________

# Use variable for creating a temp file for printing
temp_print_file = os.path.join(os.environ["TEMP"], "ocr_print_job.txt")

# Write AI OCR text to temp file for printing
with open(temp_print_file, "w", encoding="utf-8") as f:
    f.write(response.text)

# Send temp file to the printer
cmd = f'notepad /p "{temp_print_file}"'
subprocess.run(cmd, shell=True)

print("OCR text sent to printer.")