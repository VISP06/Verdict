"""Fake-data tests for the two interactive-flow fixes.

Fix 1: the interactive path honours the profile's
``payment_methods_user_will_consider`` / ``max_installment_months`` (like the
batch engine in main.py) instead of always assuming ``full_payment`` plus the
form's conditional methods.

Fix 2: the advisory report shows monthly counted income (one third of the
90-day window total), matching ``Assistant.situation()``.

The fake dataset clones user_07's real financial events under synthetic users
so the deterministic forecast stays realistic, and it omits
``income_classes.csv`` so ``IncomeClassifier`` falls back without any model
call in the calibration environment.
"""
import csv
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "code"))
from assistant import Assistant
from plan_dates import installment_completion_date
from terminal_flow import TerminalSession


FAKE_USERS = (
    ("fx_inst12", "installments", "12"),  # installments-only profile, generous cap
    ("fx_inst2", "installments", "2"),    # installments-only profile, tight cap
    ("fx_full", "full_payment", ""),      # full-payment-only profile, no cap
)
AS_OF = "2024-09-05"  # after user_07's settled rent day, outside its double-count quirk


@pytest.fixture(scope="module")
def fake_dataset(tmp_path_factory):
    base = tmp_path_factory.mktemp("fake_dataset")
    with open(os.path.join(ROOT, "dataset", "financial_profiles.csv")) as fh:
        reader = csv.reader(fh)
        profile_header = next(reader)
        user_07 = next(row for row in reader if row[0] == "user_07")
    with open(os.path.join(base, "financial_profiles.csv"), "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(profile_header)
        for user_id, methods, cap in FAKE_USERS:
            row = list(user_07)
            row[profile_header.index("user_id")] = user_id
            row[profile_header.index("payment_methods_user_will_consider")] = methods
            row[profile_header.index("max_installment_months")] = cap
            writer.writerow(row)

    with open(os.path.join(ROOT, "dataset", "financial_events.csv")) as fh:
        reader = csv.DictReader(fh)
        event_header = reader.fieldnames
        user_07_events = [row for row in reader if row["user_id"] == "user_07"]
    assert user_07_events, "user_07 events must exist in the real dataset"
    with open(os.path.join(base, "financial_events.csv"), "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=event_header)
        writer.writeheader()
        for user_id, _methods, _cap in FAKE_USERS:
            for row in user_07_events:
                new_row = dict(row)
                new_row["user_id"] = user_id
                new_row["event_id"] = f"{user_id}_{row['event_id']}"
                writer.writerow(new_row)

    for name in ("exchange_rates.csv", "request_payment_options.csv"):
        with open(os.path.join(ROOT, "dataset", name)) as fh:
            header = fh.readline()
        with open(os.path.join(base, name), "w") as fh:
            fh.write(header)
    # No income_classes.csv: the classifier falls back without model calls.
    return base


def _session(fake_dataset, user_id):
    assistant = Assistant(user_id, AS_OF, dataset_dir=str(fake_dataset), no_llm=True)
    return assistant, TerminalSession(user_id, AS_OF, dataset_dir=str(fake_dataset), assistant=assistant)


def test_installments_only_profile_gets_installment_verdict_and_no_wait(fake_dataset):
    assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Can I pay ₹50,000 in 6 installments?")
    blocks = session.handle("")  # deadline question: no deadline (default)
    report_text = blocks[-1]["text"]
    assert "PAY IN 6 INSTALLMENTS" in report_text
    assert "Confirm the 6-installment plan" in report_text
    # The profile does not accept paying in full, so no wait option exists.
    assert "WAIT UNTIL" not in report_text
    assert "wait" not in [option["type"] for option in session.report["ranked_options"]]
    assert "to pay in full and avoid fees" not in report_text
    # The 6-installment schedule ends beyond the 90-day horizon, so the engine
    # has no candidate; the advisor's safe fallback then corrects the row.
    completion = installment_completion_date(AS_OF, 6)
    assert completion.isoformat() > max(session.report["evidence"]["balances"])
    row = session.report["decision"]
    assert row["affordability_status"] == "affordable_with_plan"
    assert row["recommended_payment_method"] == "installments"
    assert len(row["payment_plan"].split("|")) == 6


def test_installment_menu_never_offers_counts_above_profile_cap(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst2")
    assert session.installment_counts == (2,)
    session.handle("Laptop 12k no deadline")
    blocks = session.handle("1")  # no partial payment
    menu = blocks[0]["text"]
    assert "PAY IN 2 INSTALLMENTS" in menu
    for count in ("3 months", "6 months", "12 months"):
        assert count not in menu
    # An explicit over-cap count is clamped to the profile cap with a notice.
    assistant2, session2 = _session(fake_dataset, "fx_inst2")
    session2.handle("Laptop 12k no deadline in 6 installments")
    assert session2.form["installment_count"] == 2
    assert any("Your profile allows at most 2 installments; using 2." in block["text"]
               for block in session2.last_blocks)


def test_wait_option_only_for_full_payment_profiles(fake_dataset):
    for user_id, accepts_full in (("fx_inst12", False), ("fx_full", True)):
        _assistant, session = _session(fake_dataset, user_id)
        session.handle("Gadget 150k no deadline just advise")
        report_text = session.last_blocks[-1]["text"]
        option_types = [option["type"] for option in session.report["ranked_options"]]
        assert ("wait" in option_types) is accepts_full
        assert ("WAIT UNTIL" in report_text) is accepts_full
        if accepts_full:
            assert report_text.splitlines()[0].startswith("╭─ WAIT UNTIL ")
        else:
            # The only profile-allowed safe plan is the capped default installments.
            assert "PAY IN 3 INSTALLMENTS" in report_text


def test_explicit_request_narrows_but_never_widens_the_profile(fake_dataset):
    # Installments-only profile plus an explicit installment decline: no
    # considered method remains, so the deterministic report is NOT RECOMMENDED.
    _assistant, session = _session(fake_dataset, "fx_inst12")
    blocks = session.handle("Can I pay ₹50,000 without any installments no deadline just advise")
    assert "NOT RECOMMENDED" in blocks[-1]["text"]
    row = session.report["decision"]
    assert row["affordability_status"] == "not_affordable"
    assert row["recommended_payment_method"] == "not_recommended"
    assert row["payment_plan"] == "none"

    # Full-payment-only profile: an installment request cannot add methods.
    _assistant2, session2 = _session(fake_dataset, "fx_full")
    session2.handle("Can I pay ₹50,000 in 6 installments?")
    blocks2 = session2.handle("")  # deadline question: no deadline (default)
    assert "PAY IN FULL TODAY" in blocks2[-1]["text"]
    assert session2.report["decision"]["recommended_payment_method"] == "full_payment"


def test_report_shows_monthly_counted_income_not_window_total(fake_dataset):
    assistant, session = _session(fake_dataset, "fx_inst12")
    form = {"amount": 50000, "currency": "INR", "item": "gadget", "pay_by_date": None,
            "allow_partial": False, "allow_installments": False}
    result = assistant.advise(form)
    window_total = sum(float(item["amount"])
                       for item in result["evidence"]["explanation_evidence"]["income_counted"])
    monthly_total = result["situation"]["monthly_income_total"]
    assert window_total == pytest.approx(3 * monthly_total)
    # The advisory situation matches the deterministic /situation command.
    situation = assistant.situation()
    assert monthly_total == situation["monthly_income_total"]
    advise_monthly = {item["series_key"]: item["amount"]
                      for item in result["situation"]["monthly_income_counted"]}
    situation_monthly = {item["series_key"]: item["amount"]
                         for item in situation["monthly_income_counted"]}
    assert advise_monthly == situation_monthly
    session.form = form
    rendered = session.render_report(result)
    assert "your salary of INR 149,000.00 lands on the 15th" in rendered
