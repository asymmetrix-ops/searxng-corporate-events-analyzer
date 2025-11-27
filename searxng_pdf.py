# searxng_pdf.py
# This module provides functionality to generate PDF reports from text data, including company details,
# summaries, corporate events, and top management, using the FPDF library. It uses the built-in Helvetica
# font to avoid external file dependencies and includes comprehensive text sanitization to replace
# non-ASCII characters and emojis with ASCII-compatible equivalents, preventing encoding errors.

from fpdf import FPDF
import io
import unicodedata

# ============================================================
# 🔹 Text Sanitization Helper
# ============================================================
def clean_text(text: str) -> str:
    """
    Sanitizes text by replacing emojis with text equivalents and non-ASCII characters with
    ASCII-compatible equivalents or placeholders to prevent encoding errors in PDF generation
    with the Helvetica font.

    Args:
        text (str): The input text to clean.

    Returns:
        str: The sanitized text with emojis and non-ASCII characters replaced.
    """
    # Return empty string if input is None or empty
    if not text:
        return ""
    
    # Define a dictionary of emojis with their text replacements
    emoji_replacements = {
        "✅": "[OK]",
        "❌": "[X]",
        "⚠️": "[Warning]",
        "🚀": "[Launch]",
        "💡": "[Idea]",
        "📈": "[Up]",
        "📉": "[Down]",
        "🏢": "[Company]",
        "👤": "[Person]",
        "📊": "[Chart]",
        "🌍": "[World]",
        "🔍": "[Search]",
        "🔗": "[Link]",
        "⭐": "[Star]",
        "🔥": "[Hot]",
        "📄": "[Doc]",
        "🧠": "[Brain]",
        "🪙": "[Coin]",
    }
    
    # Step 1: Replace emojis with their text equivalents
    for emoji, replacement in emoji_replacements.items():
        text = text.replace(emoji, replacement)
    
    # Step 2: Normalize and transliterate non-ASCII characters
    # Use NFKD normalization to decompose Unicode characters (e.g., accented letters)
    normalized_text = unicodedata.normalize('NFKD', text)
    # Convert to ASCII, replacing non-ASCII characters with their base form or ''
    ascii_text = ''.join(
        c if 32 <= ord(c) <= 126 else unicodedata.normalize('NFKD', c).encode('ascii', 'ignore').decode('ascii') or '[Non-ASCII]'
        for c in normalized_text
    )
    
    # Step 3: Replace common Unicode punctuation with ASCII equivalents
    punctuation_replacements = {
        "—": "-",  # Em dash to hyphen
        "–": "-",  # En dash to hyphen
        "’": "'",  # Right single quotation mark to straight quote
        "’": "'",  # Left single quotation mark to straight quote
        "‘": "'",  # Left single quotation mark to straight quote
        "“": '"',  # Left double quotation mark to straight quote
        "”": '"',  # Right double quotation mark to straight quote
        "…": "...",  # Ellipsis to three dots
        "™": "(TM)",  # Trademark symbol
        "®": "(R)",   # Registered trademark symbol
        "©": "(C)",   # Copyright symbol
        "€": "[Euro]",  # Euro symbol
        "£": "[Pound]",  # Pound symbol
        "¥": "[Yen]",    # Yen symbol
        "°": "[Degree]",  # Degree symbol
    }
    for char, replacement in punctuation_replacements.items():
        ascii_text = ascii_text.replace(char, replacement)
    
    return ascii_text

# ============================================================
# 🔹 PDF Generation Function
# ============================================================
def create_pdf_from_text(title: str, summary: str, description: str = "", corporate_events: str = "", top_management: str = "") -> io.BytesIO:
    """
    Generates a PDF report containing company title, description, summary, corporate events, and top management.

    Args:
        title (str): The title of the PDF, typically the company name.
        summary (str): The company summary or valuation details.
        description (str): The company description (optional, default empty).
        corporate_events (str): Corporate events data (optional, default empty).
        top_management (str): Top management data (optional, default empty).

    Returns:
        io.BytesIO: A BytesIO object containing the generated PDF data.
    """
    # Initialize FPDF object for PDF generation
    pdf = FPDF()
    pdf.add_page()
    # Enable automatic page breaks with a 15mm margin
    pdf.set_auto_page_break(auto=True, margin=15)

    # Use built-in Helvetica font for all text
    pdf.set_font("Helvetica", "B", 18)

    # Title Section
    # Add centered title with sanitized text
    pdf.multi_cell(0, 10, clean_text(title), align="C")
    pdf.ln(8)

    # Description Section
    if description:
        pdf.set_font("Helvetica", "", 12)
        # Add description with sanitized text
        pdf.multi_cell(0, 8, clean_text(description))
        pdf.ln(5)

    # Summary Section
    if summary:
        pdf.set_font("Helvetica", "", 12)
        # Add summary with sanitized text
        pdf.multi_cell(0, 8, clean_text(summary))
        pdf.ln(5)

    # Corporate Events Section
    if corporate_events:
        pdf.set_font("Helvetica", "B", 14)
        # Add section header
        pdf.multi_cell(0, 10, "Corporate Events:")
        pdf.set_font("Helvetica", "", 12)
        # Add corporate events with sanitized text
        pdf.multi_cell(0, 8, clean_text(corporate_events))
        pdf.ln(5)

    # Top Management Section
    if top_management:
        pdf.set_font("Helvetica", "B", 14)
        # Add section header
        pdf.multi_cell(0, 10, "Top Management:")
        pdf.set_font("Helvetica", "", 12)
        # Add top management with sanitized text
        pdf.multi_cell(0, 8, clean_text(top_management))

    # Generate PDF output as a string of bytes
    pdf_bytes = pdf.output(dest="S")
    # Convert bytearray to bytes if necessary
    if isinstance(pdf_bytes, bytearray):
        pdf_bytes = bytes(pdf_bytes)

    # Return the PDF data as a BytesIO object for download or further processing
    return io.BytesIO(pdf_bytes)