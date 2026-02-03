"""
Unit tests for the csv_handler module.
Tests CSV parsing, validation, and generation for channel import/export.
"""
import io
import pytest

from csv_handler import (
    parse_csv,
    validate_channel_row,
    generate_csv,
    generate_template,
    CSVParseError,
    CSVValidationError,
)


class TestParseCSV:
    """Tests for parse_csv() function."""

    def test_parses_valid_csv_all_columns(self):
        """Parses CSV with all columns correctly."""
        csv_content = """channel_number,name,group_name,tvg_id,gracenote_id,logo_url
101,ESPN HD,Sports,ESPN.US,12345,https://example.com/espn.png
102,CNN,News,CNN.US,67890,https://example.com/cnn.png"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 2
        assert len(errors) == 0
        assert rows[0]["channel_number"] == "101"
        assert rows[0]["name"] == "ESPN HD"
        assert rows[0]["group_name"] == "Sports"
        assert rows[0]["tvg_id"] == "ESPN.US"
        assert rows[0]["gracenote_id"] == "12345"
        assert rows[0]["logo_url"] == "https://example.com/espn.png"

    def test_parses_csv_with_only_required_columns(self):
        """Parses CSV with only the required 'name' column."""
        csv_content = """name
My Channel
Another Channel"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 2
        assert len(errors) == 0
        assert rows[0]["name"] == "My Channel"
        assert rows[0].get("channel_number") == ""
        assert rows[0].get("group_name") == ""

    def test_filters_comment_lines(self):
        """Lines starting with # are filtered out."""
        csv_content = """# This is a comment
channel_number,name,group_name
# Another comment
101,ESPN HD,Sports
# Final comment
102,CNN,News"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 2
        assert rows[0]["name"] == "ESPN HD"
        assert rows[1]["name"] == "CNN"

    def test_filters_comment_lines_with_whitespace(self):
        """Comment lines with leading whitespace are filtered."""
        csv_content = """channel_number,name
  # Comment with leading space
101,ESPN HD"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0]["name"] == "ESPN HD"

    def test_handles_empty_csv(self):
        """Empty CSV returns empty list."""
        csv_content = ""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 0
        assert len(errors) == 0

    def test_handles_header_only_csv(self):
        """CSV with only header returns empty list."""
        csv_content = "channel_number,name,group_name"

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 0
        assert len(errors) == 0

    def test_handles_quoted_fields_with_commas(self):
        """Fields with commas are properly quoted and parsed."""
        csv_content = """channel_number,name,group_name
101,"Sports, News, & More",Entertainment"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0]["name"] == "Sports, News, & More"

    def test_handles_empty_optional_fields(self):
        """Empty optional fields are handled gracefully."""
        csv_content = """channel_number,name,group_name,tvg_id,gracenote_id,logo_url
,My Channel,,,,"""""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0]["name"] == "My Channel"
        assert rows[0]["channel_number"] == ""
        assert rows[0]["logo_url"] == ""

    def test_strips_whitespace_from_values(self):
        """Whitespace is stripped from values."""
        csv_content = """channel_number,name,group_name
  101  ,  ESPN HD  ,  Sports  """

        rows, errors = parse_csv(csv_content)

        assert rows[0]["channel_number"] == "101"
        assert rows[0]["name"] == "ESPN HD"
        assert rows[0]["group_name"] == "Sports"

    def test_handles_utf8_encoding(self):
        """UTF-8 characters are handled correctly."""
        csv_content = """channel_number,name,group_name
101,日本テレビ,日本語
102,Ελληνικά,Ελλάδα"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 2
        assert rows[0]["name"] == "日本テレビ"
        assert rows[1]["name"] == "Ελληνικά"

    def test_returns_row_numbers_in_errors(self):
        """Errors include the row number for reference."""
        csv_content = """channel_number,name,group_name
101,ESPN HD,Sports
,,
103,CNN,News"""

        rows, errors = parse_csv(csv_content)

        # Row 3 (line 3, 1-indexed including header) has no name
        assert len(errors) == 1
        assert errors[0]["row"] == 3
        assert "name" in errors[0]["error"].lower()

    def test_missing_name_column_raises_error(self):
        """CSV without 'name' column raises CSVParseError."""
        csv_content = """channel_number,group_name
101,Sports"""

        with pytest.raises(CSVParseError) as exc_info:
            parse_csv(csv_content)

        assert "name" in str(exc_info.value).lower()

    def test_case_insensitive_column_headers(self):
        """Column headers are case-insensitive."""
        csv_content = """Channel_Number,NAME,Group_Name
101,ESPN HD,Sports"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0]["name"] == "ESPN HD"

    def test_extra_columns_ignored(self):
        """Extra columns in CSV are ignored."""
        csv_content = """channel_number,name,group_name,extra_col,another_extra
101,ESPN HD,Sports,ignored,also_ignored"""

        rows, errors = parse_csv(csv_content)

        assert len(rows) == 1
        assert "extra_col" not in rows[0]
        assert "another_extra" not in rows[0]


class TestValidateChannelRow:
    """Tests for validate_channel_row() function."""

    def test_validates_complete_row(self):
        """Complete valid row passes validation."""
        row = {
            "channel_number": "101",
            "name": "ESPN HD",
            "group_name": "Sports",
            "tvg_id": "ESPN.US",
            "gracenote_id": "12345",
            "logo_url": "https://example.com/logo.png"
        }

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 0

    def test_validates_minimal_row(self):
        """Row with only required 'name' passes validation."""
        row = {"name": "My Channel"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 0

    def test_rejects_missing_name(self):
        """Row without 'name' fails validation."""
        row = {"channel_number": "101", "group_name": "Sports"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 1
        assert "name" in errors[0].lower()

    def test_rejects_empty_name(self):
        """Row with empty 'name' fails validation."""
        row = {"name": "", "channel_number": "101"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 1
        assert "name" in errors[0].lower()

    def test_rejects_whitespace_only_name(self):
        """Row with whitespace-only 'name' fails validation."""
        row = {"name": "   ", "channel_number": "101"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 1
        assert "name" in errors[0].lower()

    def test_validates_channel_number_as_integer(self):
        """channel_number must be a valid integer if provided."""
        row = {"name": "ESPN HD", "channel_number": "abc"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 1
        assert "channel_number" in errors[0].lower()

    def test_validates_channel_number_positive(self):
        """channel_number must be positive if provided."""
        row = {"name": "ESPN HD", "channel_number": "-5"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 1
        assert "channel_number" in errors[0].lower()

    def test_accepts_decimal_channel_number(self):
        """Decimal channel numbers (e.g., 4.1) are valid."""
        row = {"name": "ESPN HD", "channel_number": "4.1"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 0

    def test_validates_logo_url_format(self):
        """logo_url must be a valid URL if provided."""
        row = {"name": "ESPN HD", "logo_url": "not-a-url"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 1
        assert "logo_url" in errors[0].lower() or "url" in errors[0].lower()

    def test_accepts_valid_http_url(self):
        """Valid HTTP URL passes validation."""
        row = {"name": "ESPN HD", "logo_url": "http://example.com/logo.png"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 0

    def test_accepts_valid_https_url(self):
        """Valid HTTPS URL passes validation."""
        row = {"name": "ESPN HD", "logo_url": "https://example.com/logo.png"}

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 0

    def test_accepts_empty_optional_fields(self):
        """Empty optional fields are valid."""
        row = {
            "name": "ESPN HD",
            "channel_number": "",
            "group_name": "",
            "tvg_id": "",
            "gracenote_id": "",
            "logo_url": ""
        }

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 0

    def test_returns_multiple_errors(self):
        """Multiple validation errors are returned together."""
        row = {
            "name": "",
            "channel_number": "abc",
            "logo_url": "not-a-url"
        }

        errors = validate_channel_row(row, row_num=1)

        assert len(errors) == 3


class TestGenerateCSV:
    """Tests for generate_csv() function."""

    def test_generates_csv_with_all_columns(self):
        """Generates CSV with all columns."""
        channels = [
            {
                "channel_number": 101,
                "name": "ESPN HD",
                "group_name": "Sports",
                "tvg_id": "ESPN.US",
                "gracenote_id": "12345",
                "logo_url": "https://example.com/espn.png"
            }
        ]

        csv_output = generate_csv(channels)

        assert "channel_number,name,group_name,tvg_id,gracenote_id,logo_url" in csv_output
        assert "101,ESPN HD,Sports,ESPN.US,12345,https://example.com/espn.png" in csv_output

    def test_generates_csv_with_empty_values(self):
        """Handles channels with missing optional fields."""
        channels = [
            {
                "channel_number": None,
                "name": "My Channel",
                "group_name": "",
                "tvg_id": None,
                "gracenote_id": None,
                "logo_url": None
            }
        ]

        csv_output = generate_csv(channels)
        lines = csv_output.strip().split("\n")

        assert len(lines) == 2  # Header + 1 data row
        assert "My Channel" in lines[1]

    def test_generates_csv_escapes_commas(self):
        """Values with commas are properly quoted."""
        channels = [
            {
                "channel_number": 101,
                "name": "Sports, News, & More",
                "group_name": "Entertainment",
                "tvg_id": "",
                "gracenote_id": "",
                "logo_url": ""
            }
        ]

        csv_output = generate_csv(channels)

        assert '"Sports, News, & More"' in csv_output

    def test_generates_empty_csv_for_no_channels(self):
        """Returns header only when no channels provided."""
        channels = []

        csv_output = generate_csv(channels)
        lines = csv_output.strip().split("\n")

        assert len(lines) == 1  # Header only
        assert "channel_number,name" in lines[0]

    def test_handles_integer_channel_numbers(self):
        """Integer channel numbers are converted to strings."""
        channels = [
            {"channel_number": 101, "name": "ESPN HD"}
        ]

        csv_output = generate_csv(channels)

        assert "101" in csv_output

    def test_handles_none_values(self):
        """None values are converted to empty strings."""
        channels = [
            {"channel_number": None, "name": "ESPN HD", "group_name": None}
        ]

        csv_output = generate_csv(channels)
        lines = csv_output.strip().split("\n")

        # Should not contain "None" as a string
        assert "None" not in csv_output


class TestGenerateTemplate:
    """Tests for generate_template() function."""

    def test_template_has_header_row(self):
        """Template includes the header row."""
        template = generate_template()

        assert "channel_number,name,group_name,tvg_id,gracenote_id,logo_url" in template

    def test_template_has_comments(self):
        """Template includes instructional comments."""
        template = generate_template()

        assert template.startswith("#")
        assert "Required" in template or "required" in template

    def test_template_has_example_row(self):
        """Template includes an example commented row."""
        template = generate_template()

        # Should have at least one commented example
        lines = template.split("\n")
        comment_lines = [l for l in lines if l.strip().startswith("#")]
        example_lines = [l for l in comment_lines if "101" in l or "ESPN" in l.lower()]

        assert len(example_lines) >= 1

    def test_template_is_valid_csv(self):
        """Template can be parsed as valid CSV (ignoring comments)."""
        template = generate_template()

        # Remove comment lines and try to parse
        non_comment_lines = [l for l in template.split("\n")
                           if l.strip() and not l.strip().startswith("#")]
        csv_content = "\n".join(non_comment_lines)

        # Should have at least a header
        assert "name" in csv_content.lower()


class TestCSVRoundTrip:
    """Tests for CSV export/import round-trip consistency."""

    def test_export_import_round_trip(self):
        """Channels exported to CSV can be imported back identically."""
        original_channels = [
            {
                "channel_number": 101,
                "name": "ESPN HD",
                "group_name": "Sports",
                "tvg_id": "ESPN.US",
                "gracenote_id": "12345",
                "logo_url": "https://example.com/espn.png"
            },
            {
                "channel_number": 102,
                "name": "CNN",
                "group_name": "News",
                "tvg_id": "CNN.US",
                "gracenote_id": "67890",
                "logo_url": "https://example.com/cnn.png"
            }
        ]

        # Export to CSV
        csv_output = generate_csv(original_channels)

        # Import back
        imported_rows, errors = parse_csv(csv_output)

        assert len(errors) == 0
        assert len(imported_rows) == 2

        # Verify data matches (channel_number becomes string)
        assert imported_rows[0]["name"] == "ESPN HD"
        assert imported_rows[0]["channel_number"] == "101"
        assert imported_rows[1]["name"] == "CNN"
        assert imported_rows[1]["channel_number"] == "102"
