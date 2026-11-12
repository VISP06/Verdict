"""Fake-data tests for installment follow-ups and deadline conflicts.

Bug 1: "Change number of installments to be 3" & co. amend the request that is
already on the table (same amount, same item, same deadline) instead of being
parsed as a new INR 3.00 request, and a bare number after to/be/into with no
currency and no purchase cue is never taken as a new amount.

Bug 2: when no allowed installment plan completes by the kept deadline, the
first flow's conflict question is asked instead of headlineing a late plan, and
a report that still ends without a compliant option is NOT RECOMMENDED.

Refinements: the conflict question only appears once installments are actually
chosen (in the sentence or by answering "Use installments?"), the fallback
alternative wording is accurate about earlier but disallowed options, and item
extraction drops the relative pronoun ("laptop", not "laptop that").

The fake dataset clones user_07's real financial events under synthetic users
and omits ``income_classes.csv`` so no model call happens (same approach as
``tests/test_profile_honouring.py``).
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
    ("fx_full", "full_payment", ""),      # full-payment-only profile, no cap
)
AS_OF = "2024-09-05"
# With the first installment paid on the as-of date: 3 -> 2024-11-04, 6 -> 2025-02-02.
COUNT3_DATE = installment_completion_date(AS_OF, 3).isoformat()
COUNT6_DATE = installment_completion_date(AS_OF, 6).isoformat()


@pytest.fixture(scope="module")
def fake_dataset(tmp_path_factory):
    base = tmp_path_factory.mktemp("fake_dataset_followups")
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


def _session(fake_dataset, user_id="fx_inst12"):
    assistant = Assistant(user_id, AS_OF, dataset_dir=str(fake_dataset), no_llm=True)
    return assistant, TerminalSession(user_id, AS_OF, dataset_dir=str(fake_dataset), assistant=assistant)


@pytest.mark.parametrize("followup", [
    "Change number of installments to be 3",
    "change number of installments to 3",
    "make it 3 installments",
    "switch to 3 installments",
    "use 3 instead",
    "split into 3",
])
def test_followup_phrasings_keep_amount_item_and_never_reask_deadline(fake_dataset, followup):
    _assistant, session = _session(fake_dataset)
    session.handle("Can I pay ₹50,000 in 6 installments?")
    blocks = session.handle("")  # deadline question: no deadline (default)
    assert "PAY IN 6 INSTALLMENTS" in blocks[-1]["text"]
    item_before = session.form["item"]

    blocks = session.handle(followup)
    assert session.form["amount"] == 50000.0
    assert session.form["item"] == item_before
    assert session.form["installment_count"] == 3
    assert session.form["allow_installments"] is True
    assert session.form["pay_by_date"] is None  # the previous deadline is kept
    assert any(block["kind"] == "report" for block in blocks)
    assert "PAY IN 3 INSTALLMENTS" in blocks[-1]["text"]
    assert "INR 50,000.00" in blocks[-1]["text"]
    # No intake question is repeated and no phantom INR 3.00 request appears.
    assert not any("When do you need to pay?" in block["text"] for block in blocks)
    assert not any(block["text"].startswith("Got it:") for block in blocks)
    assert not any("INR 3.00" in block["text"] for block in blocks)


def test_reported_request_with_cue_and_currency_is_still_new(fake_dataset):
    _assistant, session = _session(fake_dataset)
    session.handle("Can I pay ₹50,000 in 6 installments?")
    session.handle("")  # no deadline
    blocks = session.handle("Can I pay ₹40,000 in 3 installments?")
    assert session.form["amount"] == 40000.0  # a real new request, not a count amendment


def test_bare_number_after_to_is_never_a_new_amount(fake_dataset):
    _assistant, session = _session(fake_dataset)
    session.handle("Can I pay ₹50,000 in 6 installments?")
    session.handle("")  # no deadline
    blocks = session.handle("set it to 3")
    assert session.form["amount"] == 50000.0
    assert not any(block["kind"] == "report" for block in blocks)
    assert "I didn't understand that." in blocks[0]["text"]


def test_compliant_count_change_keeps_the_previous_deadline(fake_dataset):
    _assistant, session = _session(fake_dataset)
    blocks = session.handle("Can I pay ₹50,000 in 3 installments by 2024-11-15?")
    assert session.form["pay_by_date"] == "2024-11-15"
    assert any(block["kind"] == "report" for block in blocks)

    blocks = session.handle("make it 2 installments")  # completes 2024-10-05, still in time
    assert session.form["pay_by_date"] == "2024-11-15"  # deadline kept, never re-asked
    assert session.form["amount"] == 50000.0
    assert session.form["installment_count"] == 2
    assert not any("When do you need to pay?" in block["text"] for block in blocks)
    assert "PAY IN 2 INSTALLMENTS" in blocks[-1]["text"]


def test_followup_without_compliant_plan_asks_conflict_question(fake_dataset):
    _assistant, session = _session(fake_dataset)
    session.handle("Can I pay ₹50,000 in 3 installments by 2024-11-15?")
    assert any(block["kind"] == "report" for block in session.last_blocks)

    blocks = session.handle("Change number of installments to be 6")
    assert any("conflicts with the deadline 2024-11-15" in block["text"] for block in blocks)
    prompt = blocks[-1]
    assert "after that deadline" in prompt["text"]
    assert not any(block["kind"] == "report" for block in blocks)
    assert session.phase == "intake"
    # Installments-only profile: no pay-in-full escape hatch is offered.
    assert [option["label"] for option in prompt["data"]["options"]] == ["Remove the deadline"]

    blocks = session.handle("1")  # remove the deadline
    assert session.form["pay_by_date"] is None
    assert "PAY IN 6 INSTALLMENTS" in blocks[-1]["text"]


def test_no_plan_completes_by_deadline_so_conflict_question_replaces_menu(fake_dataset):
    _assistant, session = _session(fake_dataset)
    blocks = session.handle("Can I pay ₹50,000 in installments by 2024-09-20?")
    assert any("No installment option completes by 2024-09-20" in block["text"] for block in blocks)
    prompt = blocks[-1]
    assert "after that deadline" in prompt["text"]
    assert session.questions == ["deadline_conflict"]
    assert not any(block["kind"] == "report" for block in blocks)

    blocks = session.handle("1")  # remove the deadline
    assert session.form["pay_by_date"] is None
    assert "How many installments?" in blocks[-1]["text"]  # the count is asked again
    blocks = session.handle("")  # default: the shortest plan
    assert session.form["installment_count"] == 3
    assert "PAY IN 3 INSTALLMENTS" in blocks[-1]["text"]


def test_skipping_the_conflict_gives_not_recommended_deadline_verdict(fake_dataset):
    _assistant, session = _session(fake_dataset)
    blocks = session.handle("Can I pay ₹50,000 in 6 installments by 2024-09-20?")
    assert any("conflicts with the deadline 2024-09-20" in block["text"] for block in blocks)
    blocks = session.handle("/skip")  # ends with no deadline-compliant option
    report_text = blocks[-1]["text"]
    assert "NOT RECOMMENDED: no allowed plan completes by 2024-09-20" in report_text
    assert "PAY IN 6 INSTALLMENTS" not in report_text
    assert "No payment method you would consider completes by 2024-09-20" in report_text
    assert f"the shortest allowed plan ends on {COUNT3_DATE}" in report_text
    # The profile is installments-only, so pay-in-full is never offered as the
    # fallback in the alternatives (the OPTIONS table still lists the evaluated
    # scenarios as evidence).
    alts_section = report_text.split("ALTERNATIVES\n", 1)[1].split("\n\nNEXT STEPS\n")[0]
    assert "pay in full" not in alts_section
    installments_row = next(row for row in session.report_data(session.report)["options"]
                            if row["name"] == "installments")
    assert installments_row["meets_deadline"] == "no"
    assert session.report_data(session.report)["status"] == "bad"


def test_conflict_prompt_offers_full_payment_only_when_profile_allows(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    blocks = session.handle("Can I pay ₹50,000 in 6 installments by 2024-09-20?")
    labels = [option["label"] for option in blocks[-1]["data"]["options"]]
    assert labels == ["Remove the deadline"]

    _assistant2, session2 = _session(fake_dataset, "fx_full")
    blocks2 = session2.handle("Can I pay ₹50,000 in 6 installments by 2024-09-20?")
    options2 = blocks2[-1]["data"]["options"]
    assert [option["label"] for option in options2] == ["Remove the deadline", "Pay in full today instead"]
    blocks2 = session2.handle("2")  # take the option that meets the deadline
    assert session2.form["pay_by_date"] == "2024-09-20"  # the deadline is kept
    assert session2.form["allow_installments"] is False
    assert "PAY IN FULL TODAY" in blocks2[-1]["text"]


def test_installments_not_mentioned_before_asking_when_not_chosen(fake_dataset):
    # Deadline stated in the sentence, installments never chosen: the intake
    # goes straight to its own questions and only mentions installments when
    # it asks "Use installments?".
    _assistant, session = _session(fake_dataset)
    blocks = session.handle("Laptop 12k end of this month")
    assert session.form["pay_by_date"] == "2024-09-30"
    assert not any("installment" in block["text"].lower() for block in blocks)
    blocks = session.handle("1")  # partial: no
    assert "Use installments?" in blocks[-1]["text"]
    blocks = session.handle("2")  # 3 months: finishes 2024-11-04, after the deadline
    assert any("conflicts with the deadline 2024-09-30" in block["text"] for block in blocks)
    blocks = session.handle("1")  # remove the deadline
    assert session.form["pay_by_date"] is None
    assert "PAY IN 3 INSTALLMENTS" in blocks[-1]["text"]


def test_deadline_question_answer_does_not_pre_filter_unchosen_installments(fake_dataset):
    # Deadline answered as a question with no installments chosen: no
    # "No installment option completes by..." message before asking.
    _assistant, session = _session(fake_dataset)
    session.handle("Laptop 12k")
    blocks = session.handle("2")  # within 14 days -> 2024-09-19
    assert not any("No installment option completes by" in block["text"] for block in blocks)
    assert "Is partial payment acceptable?" in blocks[-1]["text"]
    blocks = session.handle("1")  # partial: no
    assert "Use installments?" in blocks[-1]["text"]
    blocks = session.handle("2")  # 3 months conflicts with the deadline
    assert any("conflicts with the deadline 2024-09-19" in block["text"] for block in blocks)
    blocks = session.handle("1")  # remove the deadline
    assert session.form["pay_by_date"] is None
    assert "PAY IN 3 INSTALLMENTS" in blocks[-1]["text"]


def test_earlier_disallowed_safe_options_wording(fake_dataset):
    # Paying in full today would finish earlier and stays safe, but the
    # installments-only profile does not accept it: say so instead of claiming
    # the chosen plan is the earliest safe way.
    _assistant, session = _session(fake_dataset)
    session.handle("Laptop 12k no deadline just advise")
    alts = session.render_report(session.report).split("ALTERNATIVES\n", 1)[1].split("\n\nNEXT STEPS\n")[0]
    assert "Earlier options exist but aren't among the payment methods you accept." in alts
    assert "The selected plan is the earliest way" not in alts
    full_option = next(option for option in session.report["ranked_options"]
                       if option["type"] == "pay_in_full_now")
    assert full_option["safe"] is True
    assert max(full_option["dates"]) < max(session.report["recommended_option"]["dates"])

    # When no earlier safe option exists the original wording stays accurate.
    _assistant2, session2 = _session(fake_dataset)
    session2.handle("Gadget 120k no deadline just advise")
    alts2 = session2.render_report(session2.report).split("ALTERNATIVES\n", 1)[1].split("\n\nNEXT STEPS\n")[0]
    assert "The selected plan is the earliest way to proceed safely without overdraft risk." in alts2
    assert "Earlier options exist" not in alts2
    assert not any(option["safe"] for option in session2.report["ranked_options"]
                   if option["type"] != "installments")


def test_item_extraction_drops_relative_pronoun(fake_dataset):
    assert TerminalSession._request_item("Can I afford a laptop that costs 80000?") == "laptop"
    assert TerminalSession._request_item("Can I afford a laptop that costs 80000? ") == "laptop"
    _assistant, session = _session(fake_dataset)
    blocks = session.handle("Can I afford a laptop that costs 80000?")
    assert "Got it: INR 80,000.00 · laptop · no deadline given yet." == blocks[0]["text"]
