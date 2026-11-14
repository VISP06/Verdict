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
    ("fx_all3", "full_payment|partial_payment|installments", "12"),  # every method
    ("fx_partial", "partial_payment", ""),  # partial-only profile, no cap
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
    # Employer messages for the payroll-note and based-on tests. Retrieval
    # hardcodes the real dataset, so the tests inject the retrieved message
    # ids into the report's evidence themselves.
    with open(os.path.join(base, "messages.csv"), "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["message_id", "user_id", "request_id", "related_event_id",
                         "sent_at", "source_type", "message_text"])
        writer.writerow(["message_fx_emp", "fx_inst12", "", "", "2024-08-29T09:30:00Z", "employer",
                         "BrightPath Media has updated your payroll record. Your confirmed salary is now "
                         "expected on 2024-09-23. Please use the revised date for anything you normally pay "
                         "around payday. Payroll ref EMP-0099."])
        writer.writerow(["message_fx_match", "fx_full", "", "", "2024-08-20T09:30:00Z", "employer",
                         "BrightPath Media confirms your regular salary. The next payroll runs on 2024-09-15. "
                         "Payroll ref EMP-0098."])
        writer.writerow(["message_fx_future", "fx_all3", "", "", "2024-09-30T09:30:00Z", "employer",
                         "BrightPath Media has updated your payroll record. Your confirmed salary is now "
                         "expected on 2024-09-23. Payroll ref EMP-0097."])
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
    assert any("No installment plan can finish by 2024-09-20" in block["text"] for block in blocks)
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

    # A profile without installments never reaches the conflict question: the
    # named method is refused at intake and the allowed method is used instead.
    _assistant2, session2 = _session(fake_dataset, "fx_full")
    blocks2 = session2.handle("Can I pay ₹50,000 in 6 installments by 2024-09-20?")
    joined2 = " ".join(block["text"] for block in blocks2)
    assert "Your profile doesn't include installments, so I'll use paying in full instead." in joined2
    ack2 = next(block["text"] for block in blocks2 if block["text"].startswith("Got it:"))
    assert "6 installments" not in ack2
    assert not any(block["kind"] == "prompt" for block in blocks2)
    assert "PAY IN FULL TODAY" in blocks2[-1]["text"]
    assert session2.form["pay_by_date"] == "2024-09-20"  # the deadline is kept
    assert session2.form["allow_installments"] is False


def test_installments_not_mentioned_before_asking_when_not_chosen(fake_dataset):
    # The installments-only profile states its rule instead of asking, and the
    # pending count question is raised as a deadline conflict right away.
    _assistant, session = _session(fake_dataset)
    blocks = session.handle("Laptop 12k end of this month")
    assert session.form["pay_by_date"] == "2024-09-30"
    joined = " ".join(block["text"] for block in blocks)
    assert "Your profile only allows installments." in joined
    assert "No installment plan can finish by" in joined
    assert "How many installments?" not in joined
    blocks = session.handle("1")  # remove the deadline -> the count still needs an answer
    assert session.form["pay_by_date"] is None
    assert "How many installments?" in blocks[-1]["text"]
    blocks = session.handle("")  # default 3-month plan
    assert "PAY IN 3 INSTALLMENTS" in blocks[-1]["text"]


def test_deadline_question_answer_does_not_pre_filter_unchosen_installments(fake_dataset):
    # Deadline answered as a question with no method chosen: no
    # "No installment plan can finish by..." message before asking.
    _assistant, session = _session(fake_dataset, "fx_all3")
    session.handle("Laptop 12k")
    blocks = session.handle("2")  # within 14 days -> 2024-09-19
    assert not any("No installment plan can finish by" in block["text"] for block in blocks)
    assert "How would you like to pay?" in blocks[-1]["text"]
    blocks = session.handle("4")  # installments chosen -> conflict raised at once
    assert any("No installment plan can finish by 2024-09-19" in block["text"] for block in blocks)
    blocks = session.handle("1")  # remove the deadline -> the count still needs an answer
    assert session.form["pay_by_date"] is None
    assert "How many installments?" in blocks[-1]["text"]
    blocks = session.handle("")
    assert any(block["kind"] == "report" for block in blocks)


def test_earlier_disallowed_safe_options_wording(fake_dataset):
    # Paying in full today would finish earlier and stays safe, but the
    # installments-only profile does not accept it: say so instead of claiming
    # the chosen plan is the earliest safe way.
    _assistant, session = _session(fake_dataset)
    session.handle("Laptop 12k no deadline just advise")
    alts = session.render_report(session.report).split("ALTERNATIVES\n", 1)[1].split("\n\nNEXT STEPS\n")[0]
    assert "Paying in full today would be safe, but it isn't among the payment methods you accept." in alts
    assert "Earlier options exist" not in alts
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
    assert "Got it: INR 80,000.00 · laptop · installments · no deadline given yet." == blocks[0]["text"]


def test_profile_gates_questions_chips_and_neutral_followups(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    blocks = session.handle("Laptop 12k")
    prompt_text = " ".join(block["text"] for block in blocks)
    assert "Is partial payment acceptable?" not in prompt_text
    blocks = session.handle("")
    prompt_text = " ".join(block["text"] for block in blocks)
    assert "How many installments?" in prompt_text
    labels = [label for label, _value in session.quick_replies()]
    assert "Pay half now" not in labels
    assert "Wait a month" not in labels
    assert "Use 3 installments" in labels
    session.handle("1")
    blocks = session.handle("wait a month")
    assert blocks[0]["text"].startswith("No change:")
    assert "installments only" in blocks[0]["text"]


def test_plain_why_and_why_not_now_use_distinct_evidence(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Laptop 12k no deadline just advise")
    report = session.report
    rendered = session.render_report(report)
    why = rendered.split("WHY\n", 1)[1].split("\n\nNUMBERS", 1)[0]
    assert "[" not in why
    assert "No unsettled bank items affect this" in why
    assert why.splitlines()[-1] == "Based on: your profile, your bank history and your salary pattern."
    not_now = session.handle("why not now")[0]["text"]
    assert "Yes — you can pay the full ₹12,000 today." in not_now
    assert "But you told us you only consider installments." in not_now
    assert "[" not in not_now
    assert not_now.splitlines()[-1] == why.splitlines()[-1]
    assert not_now != why


def test_method_question_lists_only_profile_methods(fake_dataset):
    # Multi-method profile: one question with all four choices.
    _assistant, session = _session(fake_dataset, "fx_all3")
    blocks = session.handle("Laptop 12k")
    assert "When do you need to pay?" in blocks[-1]["text"]
    blocks = session.handle("")  # no deadline -> method question
    assert "How would you like to pay?" in blocks[-1]["text"]
    prompt = blocks[-1]["text"]
    assert "1 Let Verdict decide (default)" in prompt
    assert "2 Pay in full today" in prompt
    assert "3 Pay part now and the rest later" in prompt
    assert "4 Pay in installments" in prompt
    # Single-method profiles never get the question; they get the rule instead.
    _assistant2, session2 = _session(fake_dataset, "fx_full")
    blocks2 = session2.handle("Laptop 12k")
    joined = " ".join(block["text"] for block in blocks2)
    assert "Your profile only allows paying in full." in joined
    assert session2.questions == ["deadline"]


def test_single_partial_method_goes_straight_to_amount(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_partial")
    blocks = session.handle("Laptop 12k")
    joined = " ".join(block["text"] for block in blocks)
    assert "Your profile only allows partial payments." in joined
    assert session.questions == ["deadline", "partial_amount"]
    blocks = session.handle("")  # no deadline -> amount question
    assert "How much do you want to pay now?" in blocks[-1]["text"]
    blocks = session.handle("")  # safest amount (whole amount fits the capacity)
    assert session.form["allow_partial"] is True
    assert session.form["partial_amount"] == 12000
    assert any(block["kind"] == "report" for block in blocks)


def test_let_verdict_decide_uses_every_profile_method(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_all3")
    session.handle("Laptop 12k")
    session.handle("")  # no deadline
    blocks = session.handle("")  # Let Verdict decide
    assert any(block["kind"] == "report" for block in blocks)
    assert session.form["allow_partial"] is True
    assert session.form["allow_installments"] is False
    assert "PAY IN FULL TODAY" in blocks[-1]["text"]


def test_partial_amount_question_defaults_to_safest_and_clamps(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_all3")
    session.handle("Laptop 150k")
    session.handle("")  # no deadline
    blocks = session.handle("3")  # pay part now
    prompt = blocks[-1]["text"]
    assert "INR 103,080.99 now (safest, default)" in prompt
    assert "INR 75,000.00 now (half)" in prompt
    blocks = session.handle("120000")  # typed amount above the safe capacity
    assert any("Only INR 103,080.99 is safe to pay today; using it." in block["text"]
               for block in blocks)
    assert abs(session.form["partial_amount"] - 103080.99) < 0.01
    assert any(block["kind"] == "report" for block in blocks)


def test_partial_amount_rejects_above_amount_then_defaults(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_all3")
    session.handle("Laptop 150k")
    session.handle("")  # no deadline
    session.handle("3")  # pay part now
    blocks = session.handle("200000")  # above the requested amount
    assert "Enter an amount up to INR 150,000.00" in blocks[0]["text"]
    blocks = session.handle("200000")  # second miss falls back to the safest amount
    assert any(block["kind"] == "report" for block in blocks)
    assert abs(session.form["partial_amount"] - 103080.99) < 0.01


def test_not_recommended_why_has_no_unqualified_safe_claim(fake_dataset):
    _assistant, session = _session(fake_dataset)
    session.handle("Laptop 500k no deadline just advise")
    text = session.handle("why not now")[0]["text"]
    assert "No — you cannot pay the full ₹500,000 today." in text
    assert "would be safe" not in text
    assert "You could pay up to ₹103,081 today and stay above your minimum." in text


def test_safe_but_profile_excluded_payment_is_disclosed(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Laptop 12k no deadline just advise")
    alternatives = session.render_report(session.report).split("ALTERNATIVES\n", 1)[1]
    assert "Paying in full today would be safe, but it isn't among the payment methods you accept." in alternatives


def test_installment_chip_never_offers_the_verdict_count(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Laptop 12k no deadline just advise")  # verdict: 3 installments
    labels = [label for label, _value in session.quick_replies()]
    assert "Use 3 installments" not in labels     # never the count the verdict already uses
    assert "Use 6 installments" in labels         # another count the profile allows
    assert "/why" not in labels                   # the /why command stays, its chip is gone
    assert "Why not now?" in labels
    blocks = session.handle("6 installments")     # take the chip
    assert any(block["kind"] == "report" for block in blocks)
    labels = [label for label, _value in session.quick_replies()]
    assert "Use 6 installments" not in labels
    assert "Use 3 installments" in labels


def test_installment_chip_never_offers_a_count_the_deadline_rules_out(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    # 3 monthly payments finish 2024-11-04, in time; 6 finish 2025-02-02, late.
    session.handle("Can I pay ₹50,000 in 3 installments by 2024-11-15?")
    labels = [label for label, _value in session.quick_replies()]
    assert not any(label.startswith("Use ") for label in labels)  # 3 is the verdict, 6 misses
    assert "Why not now?" in labels


def test_chip_sets_follow_the_profile(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_full")
    session.handle("Laptop 12k no deadline just advise")
    assert [label for label, _value in session.quick_replies()] == ["Wait a month", "Why not now?"]

    _assistant2, session2 = _session(fake_dataset, "fx_partial")
    session2.handle("Laptop 12k no deadline just advise")
    assert [label for label, _value in session2.quick_replies()] == ["Pay half now", "Why not now?"]

    _assistant3, session3 = _session(fake_dataset, "fx_all3")
    session3.handle("Laptop 12k no deadline just advise")
    assert [label for label, _value in session3.quick_replies()] == [
        "Pay half now", "Wait a month", "Use 3 installments", "Why not now?"]


def test_count_question_states_the_deadline_note(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Laptop 12k")
    session.handle("4")                 # pick a date
    blocks = session.handle("2024-11-15")
    prompt = blocks[-1]
    assert prompt["text"] == (f"How many installments? [1 3 months (default)] "
                              f"Only 3 monthly payments finish by 2024-11-15 "
                              f"(a 6-month plan would end {COUNT6_DATE}).")
    assert prompt["data"]["question"] == (f"How many installments? Only 3 monthly payments finish by "
                                          f"2024-11-15 (a 6-month plan would end {COUNT6_DATE}).")


def test_count_question_note_lists_every_eligible_count(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Can I pay ₹50,000 in installments by 2025-02-15?")
    prompt = session.last_blocks[-1]["text"]
    assert prompt == ("How many installments? [1 3 months (default), 2 6 months] "
                      "Only 3 and 6 monthly payments finish by 2025-02-15 "
                      "(a 12-month plan would end 2025-08-01).")


def test_count_question_has_no_note_when_the_deadline_admits_every_count(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Can I pay ₹50,000 in installments by 2025-09-15?")
    prompt = session.last_blocks[-1]["text"]
    assert prompt == "How many installments? [1 3 months (default), 2 6 months, 3 12 months]"


def test_why_and_why_not_now_are_clean_prose(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Laptop 12k no deadline just advise")
    rendered = session.render_report(session.report)
    why = rendered.split("WHY\n", 1)[1].split("\n\nNUMBERS", 1)[0]
    assert "[" not in why and "]" not in why
    lines = why.splitlines()
    assert len(lines) >= 6  # short separate lines, not one run-on paragraph
    assert "Before this payment," in why   # the baseline figure states its assumption
    assert "₹" in why and "INR" not in why  # whole-number prose currency
    assert " 2024-" not in why and " 2025-" not in why  # short dates like 13 Sep
    assert " on 13 Sep" in why
    assert lines[-1] == "Based on: your profile, your bank history and your salary pattern."
    not_now = session.handle("why not now")[0]["text"]
    nn_lines = not_now.splitlines()
    assert nn_lines[0].startswith(("Yes —", "No —"))
    assert nn_lines[1].startswith("If you pay it all today,")  # only the post-payment figure
    assert any(line.startswith("Coming up:") for line in nn_lines)
    assert any(line.startswith("Income:") for line in nn_lines)
    # The safe line quotes the real room above the minimum, not the request-capped amount.
    assert "You could pay up to ₹103,081 today and stay above your minimum." in not_now
    assert "[" not in not_now
    assert nn_lines[-1] == "Based on: your profile, your bank history and your salary pattern."


def test_employer_payroll_mismatch_note_is_honest(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_inst12")
    session.handle("Laptop 12k no deadline just advise")
    report = session.report
    report["evidence"]["retrieved_message_ids"] = ["message_fx_emp"]
    rendered = session.render_report(report)
    why = rendered.split("WHY\n", 1)[1].split("\n\nNUMBERS", 1)[0]
    assert "Your employer says salary is now expected on 23 Sep; this forecast still assumes the 15th." in why
    assert "confirms this income with an updated expected payroll date" not in why
    assert why.splitlines()[-1] == ("Based on: your profile, your bank history and your salary pattern "
                                    "and your employer's update EMP-0099.")
    not_now = session.handle("why not now")[0]["text"]
    assert "Your employer says salary is now expected on 23 Sep; this forecast still assumes the 15th." in not_now
    assert "and your employer's update EMP-0099." in not_now.splitlines()[-1]


def test_employer_message_agreeing_with_forecast_gets_no_mismatch_note(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_full")
    session.handle("Laptop 12k no deadline just advise")
    report = session.report
    report["evidence"]["retrieved_message_ids"] = ["message_fx_match"]
    rendered = session.render_report(report)
    why = rendered.split("WHY\n", 1)[1].split("\n\nNUMBERS", 1)[0]
    assert "Your employer's message EMP-0098 agrees with the salary date this forecast assumes." in why
    assert "now expected on" not in why
    assert why.splitlines()[-1].endswith("and your employer's update EMP-0098.")


def test_employer_message_sent_after_as_of_is_ignored(fake_dataset):
    _assistant, session = _session(fake_dataset, "fx_all3")
    session.handle("Laptop 12k no deadline just advise")
    report = session.report
    report["evidence"]["retrieved_message_ids"] = ["message_fx_future"]
    rendered = session.render_report(report)
    why = rendered.split("WHY\n", 1)[1].split("\n\nNUMBERS", 1)[0]
    assert "employer" not in why.lower()
    assert why.splitlines()[-1] == "Based on: your profile, your bank history and your salary pattern."
