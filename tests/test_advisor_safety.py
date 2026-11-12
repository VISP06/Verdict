import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "code"))
from assistant import Assistant
from terminal_flow import TerminalSession, parse_amount


SAMPLES = pd.read_csv(os.path.join(ROOT, "dataset", "sample_requests.csv"), dtype={"user_id": str})


def _advise(user_id, as_of, form):
    assistant = Assistant(user_id, as_of, dataset_dir=os.path.join(ROOT, "dataset"), no_llm=True)
    result = assistant.advise(form)
    session = TerminalSession(user_id, as_of, dataset_dir=os.path.join(ROOT, "dataset"), assistant=assistant)
    session.form = form
    return result, session.render_report(result)


def _sample_advice(row):
    amount, currency = parse_amount(row.request_text)
    form = {
        "amount": amount,
        "currency": currency,
        "item": row.request_type,
        "pay_by_date": row.desired_completion_date,
        "allow_partial": str(row.allows_partial_payment).lower() == "true",
        "allow_installments": False,
    }
    return _advise(row.user_id, row.request_date, form)


def test_sample_safety_property_and_named_unaffordable_requests():
    no_safe_ids = {f"request_{n:02d}" for n in (5, 10, 14, 15, 20, 24, 25)}
    wait_result = None
    for _, row in SAMPLES.iterrows():
        result, report = _sample_advice(row)
        if result["headline"].startswith("Recommended:"):
            assert result["recommended_option"] is not None
            assert result["recommended_option"]["safe"] is True
        if row.request_id in no_safe_ids:
            assert result["decision"]["affordability_status"] == "not_affordable"
            assert result["recommended_option"] is None
            assert not any(option["safe"] for option in result["ranked_options"])
            assert "NOT RECOMMENDED: no option keeps your balance above the minimum" in report
            assert "SAFE" in report and "✗ UNSAFE" in report
            unsafe_note = next(line for line in report.splitlines() if "Note:" in line)
            assert "drops to" in unsafe_note
        if row.request_id == "request_03":
            wait_result = (result, report)
    assert wait_result is not None
    result, report = wait_result
    assert result["recommended_option"]["type"] == "wait"
    assert result["recommended_option"]["safe"] is True
    assert report.splitlines()[0].startswith("╭─ WAIT UNTIL ")


def test_rich_user_full_payment_does_not_create_degenerate_partial_plan():
    form = {"amount": 12000, "currency": "INR", "pay_by_date": "2025-11-17",
            "allow_partial": True, "allow_installments": False}
    result, report = _advise("user_07", "2025-11-03", form)
    # The interactive path honours the profile: user_07 considers installments
    # only, so the request resolves to the 3-installment plan (synthesized
    # default terms), never to a degenerate partial plan. No installment plan
    # completes by the 2025-11-17 deadline, so the verdict must not headline
    # the late plan.
    assert report.splitlines()[0].startswith("╭─ NOT RECOMMENDED: no allowed plan completes by 2025-11-17")
    assert result["recommended_option"]["type"] == "installments"
    assert result["recommended_option"]["meets_deadline"] is False
    assert not any(option["type"] == "partial_then_remainder" for option in result["ranked_options"])
