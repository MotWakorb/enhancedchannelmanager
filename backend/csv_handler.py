"""
CSV Handler for Channel Import/Export.

Provides functions for parsing CSV files for channel import,
validating channel data, and generating CSV exports.
"""
import csv
import io
from typing import Any
from urllib.parse import urlparse


class CSVParseError(Exception):
    """Raised when CSV parsing fails due to structural issues."""
    pass


class CSVValidationError(Exception):
    """Raised when CSV data fails validation."""
    pass


# Required and optional columns for channel CSV
REQUIRED_COLUMNS = ["name"]
OPTIONAL_COLUMNS = ["channel_number", "group_name", "tvg_id", "gracenote_id", "logo_url", "stream_urls"]
ALL_COLUMNS = ["channel_number", "name", "group_name", "tvg_id", "gracenote_id", "logo_url", "stream_urls"]


def parse_csv(csv_content: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """
    Parse CSV content into a list of channel dictionaries.

    Args:
        csv_content: Raw CSV string content

    Returns:
        Tuple of (rows, errors) where:
        - rows: List of dictionaries with channel data
        - errors: List of dictionaries with row number and error message

    Raises:
        CSVParseError: If the CSV is missing required columns
    """
    rows = []
    errors = []

    if not csv_content or not csv_content.strip():
        return rows, errors

    # Filter out comment lines (lines starting with #)
    lines = csv_content.split("\n")
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            filtered_lines.append(line)

    if not filtered_lines:
        return rows, errors

    filtered_content = "\n".join(filtered_lines)

    # Parse CSV
    reader = csv.DictReader(io.StringIO(filtered_content))

    # Normalize column names to lowercase
    if reader.fieldnames is None:
        return rows, errors

    # Create mapping from lowercase to original column names
    column_mapping = {}
    for col in reader.fieldnames:
        column_mapping[col.lower().strip()] = col

    # Check for required columns (case-insensitive)
    lowercase_fieldnames = [col.lower().strip() for col in reader.fieldnames]
    for required in REQUIRED_COLUMNS:
        if required.lower() not in lowercase_fieldnames:
            raise CSVParseError(f"Missing required column: {required}")

    # Process rows
    row_num = 1  # Start at 1 for header, data rows start at 2
    for csv_row in reader:
        row_num += 1

        # Normalize row keys to lowercase and strip whitespace
        normalized_row = {}
        for col in ALL_COLUMNS:
            # Find the original column name (case-insensitive)
            original_col = column_mapping.get(col.lower())
            if original_col and original_col in csv_row:
                value = csv_row[original_col]
                normalized_row[col] = value.strip() if value else ""
            else:
                normalized_row[col] = ""

        # Validate the row
        row_errors = validate_channel_row(normalized_row, row_num)
        if row_errors:
            for error in row_errors:
                errors.append({"row": row_num, "error": error})
        else:
            rows.append(normalized_row)

    return rows, errors


def validate_channel_row(row: dict[str, str], row_num: int) -> list[str]:
    """
    Validate a single channel row.

    Args:
        row: Dictionary with channel data
        row_num: Row number for error reporting

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Check required field: name
    name = row.get("name", "").strip()
    if not name:
        errors.append("Missing required field: name")

    # Validate channel_number if provided
    channel_number = row.get("channel_number", "").strip()
    if channel_number:
        try:
            num = float(channel_number)
            if num <= 0:
                errors.append("channel_number must be positive")
        except ValueError:
            errors.append("channel_number must be a valid number")

    # Validate logo_url if provided
    logo_url = row.get("logo_url", "").strip()
    if logo_url:
        if not _is_valid_url(logo_url):
            errors.append("logo_url must be a valid HTTP/HTTPS URL")

    return errors


def _is_valid_url(url: str) -> bool:
    """Check if a string is a valid HTTP/HTTPS URL."""
    try:
        result = urlparse(url)
        return result.scheme in ("http", "https") and bool(result.netloc)
    except Exception:
        return False


def generate_csv(channels: list[dict[str, Any]]) -> str:
    """
    Generate CSV content from a list of channels.

    Args:
        channels: List of channel dictionaries

    Returns:
        CSV string content
    """
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=ALL_COLUMNS, extrasaction="ignore")

    writer.writeheader()

    for channel in channels:
        # Normalize values: convert None to empty string, numbers to strings
        row = {}
        for col in ALL_COLUMNS:
            value = channel.get(col)
            if value is None:
                row[col] = ""
            elif isinstance(value, (int, float)):
                row[col] = str(int(value)) if isinstance(value, int) or value == int(value) else str(value)
            else:
                row[col] = str(value) if value else ""
        writer.writerow(row)

    return output.getvalue()


def generate_template() -> str:
    """
    Generate a CSV template with comments and examples.

    Returns:
        CSV template string with instructional comments
    """
    template = """# Enhanced Channel Manager - Channel Import Template
# Lines starting with # are comments and will be ignored
#
# Required field: name
# Optional fields: channel_number, group_name, tvg_id, gracenote_id, logo_url, stream_urls
#
# stream_urls: semicolon-separated list of stream URLs to link to the channel
#
# Example rows (remove # to use):
# 101,ESPN HD,Sports,ESPN.US,12345,https://example.com/espn-logo.png,http://stream1.example.com/espn.ts;http://stream2.example.com/espn.ts
# 102,CNN,News,CNN.US,67890,https://example.com/cnn-logo.png,http://stream.example.com/cnn.ts
# ,My Custom Channel,Custom,,,,
#
channel_number,name,group_name,tvg_id,gracenote_id,logo_url,stream_urls
"""
    return template
