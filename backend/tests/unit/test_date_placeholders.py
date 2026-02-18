from datetime import datetime, timedelta
from auto_creation_evaluator import ConditionEvaluator

def test_expand_date_placeholders_basic():
    evaluator = ConditionEvaluator()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Standard placeholders
    assert evaluator._expand_date_placeholders("{date}") == today
    assert evaluator._expand_date_placeholders("{today}") == today
    
    # Within text
    assert evaluator._expand_date_placeholders("prefix-{date}-suffix") == f"prefix-{today}-suffix"
    
    # No placeholders
    assert evaluator._expand_date_placeholders("no placeholders here") == "no placeholders here"
    assert evaluator._expand_date_placeholders("") == ""
    assert evaluator._expand_date_placeholders(None) is None

def test_expand_date_placeholders_format():
    evaluator = ConditionEvaluator()
    today_fmt = datetime.now().strftime("%d %b %Y")
    
    # Custom format
    assert evaluator._expand_date_placeholders("{date:%d %b %Y}") == today_fmt
    
    # Invalid format should be handled gracefully
    result = evaluator._expand_date_placeholders("{date:%Q}")
    assert result in ("%Q", "{date:%Q}")

def test_expand_date_placeholders_offset_days():
    evaluator = ConditionEvaluator()
    base_date = datetime.now()
    d0 = base_date.strftime("%Y-%m-%d")
    d1 = (base_date + timedelta(days=1)).strftime("%Y-%m-%d")
    d2 = (base_date + timedelta(days=2)).strftime("%Y-%m-%d")
    
    # Positive offset (range)
    expected_pos = f"({d0}|{d1}|{d2})"
    assert evaluator._expand_date_placeholders("{date+2}") == expected_pos
    assert evaluator._expand_date_placeholders("{date+2d}") == expected_pos

    # Negative offset (range)
    dm1 = (base_date - timedelta(days=1)).strftime("%Y-%m-%d")
    dm2 = (base_date - timedelta(days=2)).strftime("%Y-%m-%d")
    expected_neg = f"({d0}|{dm1}|{dm2})"
    assert evaluator._expand_date_placeholders("{date-2}") == expected_neg

def test_expand_date_placeholders_offset_weeks():
    evaluator = ConditionEvaluator()
    base_date = datetime.now()
    
    # 1 week = 7 days offset (8 days total including today)
    dates = [(base_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(8)]
    expected = f"({'|'.join(dates)})"
    assert evaluator._expand_date_placeholders("{date+1w}") == expected

def test_expand_date_placeholders_combined_offset_format():
    evaluator = ConditionEvaluator()
    base_date = datetime.now()
    fmt = "%m-%d"
    
    d0 = base_date.strftime(fmt)
    d1 = (base_date + timedelta(days=1)).strftime(fmt)
    
    expected = f"({d0}|{d1})"
    assert evaluator._expand_date_placeholders("{date+1:%m-%d}") == expected

def test_expand_date_placeholders_90_day_cap():
    evaluator = ConditionEvaluator()
    base_date = datetime.now()
    
    # Requesting 1000 days should cap at 90
    result = evaluator._expand_date_placeholders("{date+1000}")
    
    # It should have 91 elements (0 to 90 inclusive)
    # Regex: (d0|d1|...|d90)
    elements = result.strip("()").split("|")
    assert len(elements) == 91
    assert elements[0] == base_date.strftime("%Y-%m-%d")
    assert elements[-1] == (base_date + timedelta(days=90)).strftime("%Y-%m-%d")

    # Negative cap
    result_neg = evaluator._expand_date_placeholders("{date-1000}")
    elements_neg = result_neg.strip("()").split("|")
    assert len(elements_neg) == 91
    assert elements_neg[-1] == (base_date - timedelta(days=90)).strftime("%Y-%m-%d")

def test_expand_date_placeholders_multiple():
    evaluator = ConditionEvaluator()
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    text = "From {date} to {date+1}"
    expected = f"From {today} to ({today}|{tomorrow})"
    assert evaluator._expand_date_placeholders(text) == expected

def test_expand_date_placeholders_invalid_offset():
    evaluator = ConditionEvaluator()
    
    # These should remain unexpanded as they don't match the regex pattern
    # or the numeric parsing fails (though regex \d+ prevents most failures)
    assert evaluator._expand_date_placeholders("{date+xyz}") == "{date+xyz}"
    assert evaluator._expand_date_placeholders("{date+1x}") == "{date+1x}"
    assert evaluator._expand_date_placeholders("{today+}") == "{today+}"

def test_expand_date_placeholders_allow_ranges_false():
    evaluator = ConditionEvaluator()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Simple placeholders should still expand
    assert evaluator._expand_date_placeholders("{date}", allow_ranges=False) == today
    
    # Ranges/offsets should NOT expand if allow_ranges is False
    assert evaluator._expand_date_placeholders("{date+1}", allow_ranges=False) == "{date+1}"
    assert evaluator._expand_date_placeholders("{date+1w}", allow_ranges=False) == "{date+1w}"
    assert evaluator._expand_date_placeholders("{date-1}", allow_ranges=False) == "{date-1}"
