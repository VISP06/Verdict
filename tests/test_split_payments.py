"""Fake-data tests for the "Split into equal payments" method.

Covers the fee question (0%/1%/2%/5%/typed, capped at 5%), equal-amount
rounding with the last payment absorbing the remainder, windows shorter than
seven days (no split possible), the fewer/more follow-ups at both limits, fee
changes, the no-fitting-installment fallback to the split offer, and the
installments-only profile that is never offered the split.

The fake dataset clones user_07's real financial events under synthetic users
and omits ``income_classes.csv`` so no model call happens (same approach as
``tests/test_followup_and_deadline.py``).
"""
import csv
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "code"))
from assistant import Assistant
from terminal_flow import TerminalSession


FAKE_USERS = (
    ("fx_inst12", "installments", "12"),  # installments-only profile, generous cap
    ("fx_full", "full_payment", ""),      # full-payment-only profile, no cap
    ("fx_all3", "full_payment|partial_payment|installments", "12"),  # every method
    ("fx_partial", "partial_payment", ""),  # partial-only profile, no cap
)
AS_OF = "2024-09-05"


@pytest.fixture(scope="module")
def split_dataset(tmp_path_factory):
    base = tmp_path_factory.mktemp("fake_dataset_split")
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
    with open(os.path.join(base, "messages.csv"), "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["message_id", "user_id", "request_id", "related_event_id",
                         "sent_at", "source_type", "message_text"])
    # No income_classes.csv: the classifier falls back without model calls.
    return base


def _session(split_dataset, user_id="fx_all3"):
    assistant = Assistant(user_id, AS_OF, dataset_dir=str(split_dataset), no_llm=True)
    return assistant, TerminalSession(user_id, AS_OF, dataset_dir=str(split_dataset), assistant=assistant)


# --- Fee question: chips, typed values, the 5% cap and invalid input ---

@pytest.mark.parametrize("answer", ["9", "-3", "abc"])
def test_split_fee_rejects_above_cap_negatives_and_non_numbers(split_dataset, answer):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")               # Split into equal payments
    assert "Optional split fee percentage?" in session.last_blocks[-1]["text"]
    blocks = session.handle(answer)
    assert blocks[0]["text"] == "Maximum fee is 5%. Enter a percentage between 0 and 5, or press Enter for 0%."
    assert "Optional split fee percentage?" in blocks[-1]["text"]  # asked again
    assert session.form.get("split_fee_pct") is None


def test_split_fee_accepts_chips_and_typed_values_up_to_five(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("5")               # 5% chip: at the cap, accepted
    assert session.form["split_fee_pct"] == 5.0
    assert any(block["kind"] == "report" for block in session.last_blocks)

    _assistant2, session2 = _session(split_dataset)
    session2.handle("Laptop 12k in 14 days")
    session2.handle("5")
    session2.handle("3")              # typed value below the cap
    assert session2.form["split_fee_pct"] == 3.0


def test_inr_grouping_and_lakh_acknowledgement(split_dataset):
    _assistant, session = _session(split_dataset)
    blocks = session.handle("Laptop 1.8 lakh no deadline just advise")
    assert blocks[0]["text"].startswith("Got it: ₹1.8 lakh")
    report = blocks[-1]["text"]
    assert "₹1,80,000.00" in report
    assert "₹1,80,000" in report


def test_not_recommended_why_explains_the_chosen_installment_plan(split_dataset):
    _assistant, session = _session(split_dataset, "fx_inst12")
    session.handle("Laptop 20 lakh in 12 installments no deadline just advise")
    why = session.render_report(session.report).split("WHY\n", 1)[1].split("\n\nNUMBERS", 1)[0]
    assert "12 payments of" in why
    assert "monthly salary" in why
    assert "first falls below your minimum" in why
    assert "paying" not in why.lower() or "in full" not in why.lower()


def test_financing_snapshot_skips_an_impossible_amount(split_dataset):
    _assistant, session = _session(split_dataset, "fx_all3")
    blocks = session.handle("I want to buy a car worth 1.8 Crore. How can I finance this?")
    text = "\n".join(block["text"] for block in blocks)
    assert text.startswith("FINANCING SNAPSHOT")
    assert "Safe to pay today:" in text
    assert "Pay in full:" in text
    assert "Part now, rest later:" in text
    assert "Installments:" in text
    assert "What would fit:" in text
    assert "Based on:" in text
    assert "NOT RECOMMENDED" in text
    assert "Ask about a smaller amount" in text
    assert session.questions == []


def test_financing_snapshot_continues_for_a_feasible_amount(split_dataset):
    _assistant, session = _session(split_dataset, "fx_all3")
    blocks = session.handle("How can I finance a 60,000 laptop?")
    assert blocks[0]["text"].startswith("FINANCING SNAPSHOT")
    assert "Safe to pay today:" in blocks[0]["text"]
    assert "Based on:" in blocks[0]["text"]
    assert session.phase == "intake"
    assert session.questions
    assert any(block["kind"] == "prompt" for block in blocks)


def test_enter_accepts_the_zero_fee_default(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("")
    assert session.form["split_fee_pct"] == 0.0


# --- Equal amounts: rounding absorbed by the last payment, currency granularity ---

def test_equal_amounts_round_and_the_last_payment_absorbs_the_remainder(split_dataset):
    _assistant, session = _session(split_dataset)
    session.form = {"amount": 10000, "currency": "INR", "split_fee_pct": 3}
    total, amounts = session._split_amounts(3, 14)
    assert total == 10300.0                    # 10,000 x (1 + 3%)
    assert amounts == [3433.33, 3433.33, 3433.34]
    assert sum(amounts) == total
    # Whole units for zero-decimal currencies.
    session.form["currency"] = "IDR"
    session.form["split_fee_pct"] = 0
    total, amounts = session._split_amounts(3, 14)
    assert amounts == [3333, 3333, 3334]


def test_split_dates_pin_today_and_the_deadline(split_dataset):
    _assistant, session = _session(split_dataset)
    assert session._split_dates(2, 14) == ["2024-09-05", "2024-09-19"]
    assert session._split_dates(3, 14) == ["2024-09-05", "2024-09-12", "2024-09-19"]


def test_split_verdict_shows_per_payment_dates_and_total_with_fee(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("2")               # 2% fee
    report = blocks_text = session.last_blocks[-1]["text"]
    assert report.split("\n", 1)[0] == "╭─ SPLIT INTO 2 EQUAL PAYMENTS ─╮"
    assert "split 2 × ₹6,120.00" in report          # 12,000 x 1.02 / 2
    assert "Total with the 2% fee is ₹12,240.00" in report
    assert "2024-09-05, 2024-09-19" in report


# --- Window rules: deadline window, 30-day default, under 7 days impossible ---

def test_no_deadline_split_states_the_30_day_window(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k no deadline")
    session.handle("5")
    session.handle("")
    why = session.last_blocks[-1]["text"].split("WHY\n", 1)[1].split("\n\nNUMBERS", 1)[0]
    assert "No deadline was given, so the split assumes a 30-day window." in why


def test_window_under_seven_days_makes_the_split_impossible(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 3 days")
    session.handle("5")
    blocks = session.handle("")
    report = blocks[-1]["text"]
    assert report.split("\n", 1)[0].startswith("╭─ NOT RECOMMENDED")
    assert "Splitting into equal payments is not possible: payments must be at least 7 days apart." in report


# --- Fewer/more follow-ups at both limits, unsafe counts refused ---

def test_fewer_payments_at_the_minimum_is_explained(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("")                # verdict: split into 2 (the fewest)
    blocks = session.handle("fewer payments")
    assert blocks[0]["text"] == "No change: 2 is the minimum; 1 would be paying in full."
    assert not any(block["kind"] == "report" for block in blocks)


def test_more_payments_beyond_the_seven_day_spacing_is_explained(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("")                # verdict: split into 2
    blocks = session.handle("more payments")   # 3 payments still fit the 14 days
    assert "SPLIT INTO 3 EQUAL PAYMENTS" in blocks[-1]["text"]
    blocks = session.handle("more payments")   # 4 payments need gaps under 7 days
    assert blocks[0]["text"] == ("No change: payments must be at least 7 days apart; "
                                 "the 14-day window fits at most 3 payments.")
    assert not any(block["kind"] == "report" for block in blocks)


def test_make_it_n_payments_recomputes_and_refuses_out_of_range(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("")
    blocks = session.handle("make it 3 payments")
    assert session.form["split_count"] == 3
    assert "SPLIT INTO 3 EQUAL PAYMENTS" in blocks[-1]["text"]
    blocks = session.handle("make it 6 payments")
    assert blocks[0]["text"] == ("No change: payments must be at least 7 days apart; "
                                 "the 14-day window fits at most 3 payments.")
    assert session.form["split_count"] == 3   # the previous plan is kept


def test_unsafe_requested_count_is_refused_and_keeps_the_plan(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 300k in 14 days")
    session.handle("5")
    session.handle("")                # nothing safe -> NOT RECOMMENDED
    report = session.last_blocks[-1]["text"]
    assert report.split("\n", 1)[0].startswith("╭─ NOT RECOMMENDED")
    assert "No equal-payment split up to 3 payments keeps your balance above the minimum." in report
    blocks = session.handle("make it 3 payments")
    assert blocks[0]["text"].startswith("No change: 3 payments would drop the balance to ")
    assert "below your minimum" in blocks[0]["text"]
    assert not any(block["kind"] == "report" for block in blocks)


# --- Fee change follow-up ---

def test_fee_change_recomputes_amounts_and_an_invalid_fee_is_refused(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("2")
    blocks = session.handle("fee 3%")
    assert session.form["split_fee_pct"] == 3.0
    assert any(block["kind"] == "report" for block in blocks)
    assert "Total with the 3% fee is ₹12,360.00" in blocks[-1]["text"]
    blocks = session.handle("fee 9%")
    assert blocks[0]["text"] == "No change: the split fee must be between 0 and 5 percent."
    assert session.form["split_fee_pct"] == 3.0   # the previous plan is kept
    assert not any(block["kind"] == "report" for block in blocks)


# --- Installment fallback to the split ---

def test_no_fitting_installment_plan_offers_the_split(split_dataset):
    _assistant, session = _session(split_dataset)
    blocks = session.handle("Laptop 12k in installments in 14 days")
    assert any("No monthly installment plan finishes by 2024-09-19" in block["text"] for block in blocks)
    assert "Would you like to split it into equal payments instead?" in blocks[-1]["text"]
    blocks = session.handle("2")      # decline -> the report keeps the deadline
    assert any(block["kind"] == "report" for block in blocks)
    assert session.form["pay_by_date"] == "2024-09-19"


def test_accepted_split_offer_asks_the_fee_then_reports(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in installments in 14 days")
    session.handle("1")               # accept the split
    assert "Optional split fee percentage?" in session.last_blocks[-1]["text"]
    session.handle("")
    report = session.last_blocks[-1]["text"]
    assert report.split("\n", 1)[0] == "╭─ SPLIT INTO 2 EQUAL PAYMENTS ─╮"


# --- Profile without partial payment: never offered the split ---

def test_installments_only_profile_gets_no_split_offer(split_dataset):
    _assistant, session = _session(split_dataset, "fx_inst12")
    blocks = session.handle("Laptop 12k in 14 days")
    joined = " ".join(block["text"] for block in blocks)
    assert "No monthly installment plan finishes by 2024-09-19" in joined
    assert "Would you like to split" not in joined
    assert any(block["kind"] == "report" for block in blocks)
    assert "NOT RECOMMENDED: no allowed plan completes by 2024-09-19" in blocks[-1]["text"]


# --- Deadlines stay capped at 90 days for split follow-ups too ---

def test_split_followup_rejects_a_deadline_beyond_90_days(split_dataset):
    _assistant, session = _session(split_dataset)
    session.handle("Laptop 12k in 14 days")
    session.handle("5")
    session.handle("")
    blocks = session.handle("by 2025-01-01")
    assert blocks[0]["text"] == ("No change: deadlines can be at most 90 days ahead (until 2024-12-04); "
                                 "pick a date up to then, or choose no deadline.")
    assert session.form["pay_by_date"] == "2024-09-19"
