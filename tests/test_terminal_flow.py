import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "code"))
from terminal_flow import TerminalSession, parse_amount
from plan_dates import installment_completion_date


@pytest.mark.parametrize(("text", "expected", "currency"), [
    ("12k", 12000, None), ("12K", 12000, None), ("1.5m", 1500000, None),
    ("12,000", 12000, None), ("12 000", 12000, None), ("1.2 lakh", 120000, None),
    ("2 crore", 20000000, None), ("₹500", 500, "INR"), ("$ 500", 500, "USD"),
    ("€500 EUR", 500, "EUR"), ("R 500", 500, "ZAR"), ("500 IDR", 500, "IDR"),
    ("500 USD", 500, "USD"), ("0 INR", None, "INR"), ("no amount", None, None),
    ("Rs 500", 500, "INR"), ("Rs. 500", 500, "INR"), ("500 rupee", 500, "INR"),
    ("500 rupees", 500, "INR"),
    ("EUR 5,414.20", 5414.2, "EUR"), ("ZAR 15,488", 15488, "ZAR"),
])
def test_parse_amount(text, expected, currency):
    amount, actual_currency = parse_amount(text)
    assert amount == expected
    assert actual_currency == currency


def start(text="Can I afford a 12k laptop?"):
    session = TerminalSession("user_05", "2025-11-04")
    blocks = session.handle(text)
    return session, blocks


def test_intake_defaults_complete_report_and_partial_installment_paths():
    session, blocks = start()
    assert "When do you need" in blocks[-1]["text"]
    blocks = session.handle("")          # no deadline -> method question
    assert "How would you like to pay?" in blocks[-1]["text"]
    blocks = session.handle("")          # Let Verdict decide -> report
    assert blocks[0]["kind"] == "report"
    assert session.report
    session, _ = start("Can I pay half now for a 12k laptop?")
    assert session.form["partial_amount"] == 6000
    assert "When do you need" in session.last_blocks[-1]["text"]
    blocks = session.handle("")
    assert session.form["allow_partial"]
    assert blocks[0]["kind"] == "report"
    session, _ = start("Laptop 12k in 3 installments")
    assert session.form["allow_installments"] and session.form["installment_count"] == 3
    session, _ = start("Laptop 12k no deadline")
    blocks = session.handle("3")         # pay part now -> amount question
    assert "How much do you want to pay now?" in blocks[-1]["text"]
    blocks = session.handle("")          # safest amount -> report
    assert session.form["allow_partial"] and session.form.get("partial_amount")
    assert blocks[0]["kind"] == "report"


def test_skipped_questions_and_invalid_answers():
    session, blocks = start("Laptop 12k, no deadline")
    assert "How would you like to pay?" in blocks[-1]["text"]
    session, _ = start()
    session.handle("bogus")
    assert session.phase == "intake"
    session.handle("still bogus")
    assert session.question_index == 1
    session, _ = start()
    blocks = session.handle("/skip")
    assert blocks[0]["kind"] == "report"


def test_report_amounts_are_engine_backed():
    session, _ = start("Laptop 12k no deadline pay half now just advise")
    report = session.report
    assert report
    evidence_text = repr(report)
    for option in report["ranked_options"]:
        for amount in (option["total_cost"], option["lowest_projected_balance"]):
            assert str(amount) in evidence_text


def test_report_verdict_controls_steps_and_pending_items_are_reported_once():
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop costs Rs. 12k no deadline just advise")
    result = session.report
    rendered = session.render_report(result)
    # The interactive path honours the profile: user_07 considers installments
    # only, so the plain 12k request resolves to the 3-installment plan.
    if result["recommended_option"]:
        assert "PAY IN 3 INSTALLMENTS" in rendered
        assert "Confirm the 3-installment plan" in rendered
        assert "Do not make this payment now" not in rendered

    result["recommended_option"] = None
    result["decision"]["affordability_status"] = "not_affordable"
    result["decision"]["amount_safe_to_pay"] = 2500
    result["decision"]["spending_changes_needed"] = "reduce dining out"
    report = session.render_report(result)
    next_steps = report.split("NEXT STEPS\n", 1)[1]
    assert "NOT RECOMMENDED" in report
    assert "Do not make this payment now" in next_steps
    assert "Amount safe to pay today: INR 2,500.00" in next_steps
    assert "Earliest safe date:" in next_steps
    assert "income resumes" in next_steps and "smaller amount" in next_steps
    assert "reduce dining out" in next_steps
    assert "No unsettled bank items affect this" in report


def test_deadline_filters_installments_and_conflicting_deadline_reasks():
    # With a deadline but no method chosen, the intake asks the method question
    # first; once installments are chosen and no plan fits, the split is offered.
    session, blocks = start("Laptop 12k end of this month")
    assert session.form["pay_by_date"] == "2025-11-30"
    assert not any("finishes by" in block["text"] for block in blocks)
    assert "How would you like to pay?" in blocks[-1]["text"]
    blocks = session.handle("4")  # installments chosen: no count completes in time
    assert any("No monthly installment plan finishes by 2025-11-30" in block["text"] for block in blocks)
    assert "Would you like to split it into equal payments instead?" in blocks[-1]["text"]
    blocks = session.handle("2")  # decline the split -> the report keeps the deadline
    assert any(block["kind"] == "report" for block in blocks)
    assert session.form["pay_by_date"] == "2025-11-30"

    # If an installment count was stated first, a conflicting deadline is rejected
    # and the replacement prompt allows only no deadline or a date after completion.
    session, blocks = start("Laptop 12k in 3 installments")
    assert "When do you need" in blocks[-1]["text"]
    blocks = session.handle("2")
    assert any("conflicts with the deadline" in block["text"] for block in blocks)
    prompt = blocks[-1]["text"]
    assert "none" in prompt and "on or after 2026-01-03" in prompt
    blocks = session.handle("1")
    assert session.form["pay_by_date"] is None


def test_installment_options_for_no_deadline_and_shared_completion_date():
    session, blocks = start("Laptop 12k no deadline")
    blocks = session.handle("4")  # choose installments -> count question
    # The menu never offers counts above the profile cap; user_05 allows 4,
    # so only the 3-month plan is listed.
    assert "3 months" in blocks[0]["text"]
    assert "6 months" not in blocks[0]["text"]
    assert "12 months" not in blocks[0]["text"]
    assert installment_completion_date("2025-11-04", 3) == __import__("datetime").date(2026, 1, 3)
    result = session.assistant.advise({"amount": 12000, "currency": "ZAR", "allow_installments": True,
                                       "installment_count": 3, "installment_fee_pct": 0})
    option = next(item for item in result["ranked_options"] if item["type"] == "installments")
    assert max(option["dates"]) == installment_completion_date("2025-11-04", 3).isoformat()


def test_deadline_answer_filters_later_installments_and_why_users_are_evidence_based(monkeypatch):
    session, _ = start("Laptop costs 12k")
    blocks = session.handle("3")  # end of this month
    # No installments chosen yet: no miss message, the intake continues.
    assert not any("No monthly installment plan finishes by" in block["text"] for block in blocks)
    assert "How would you like to pay?" in blocks[-1]["text"]
    blocks = session.handle("4")  # installments chosen: no count fits -> split offered
    assert any("No monthly installment plan finishes by" in block["text"] for block in blocks)

    demo = TerminalSession("user_07", "2025-11-04")
    assert demo.assistant.engine.get_user_home_currency("user_07") == "INR"
    assert demo.handle("Laptop costs Rs. 12k no deadline just advise")[-1]["kind"] == "report"
    why = demo.handle("/why")[0]["text"]
    for label in ("Data loaded:", "Income streams/classes:", "Retrieved message IDs:",
                  "Options considered:", "Wording: plain text", "Failproof session: chat-"):
        assert label in why
    users = demo.handle("/users INR")[0]["text"]
    assert "user_07 · INR · INR" in users
    assert len(users.splitlines()) <= 9


def test_missing_direct_currency_rate_has_account_specific_message(monkeypatch):
    session = TerminalSession("user_07", "2025-11-04")
    session.form = {"amount": 10, "currency": "USD", "pay_by_date": None}
    monkeypatch.setattr(session.assistant, "_home_amount", lambda *_args: (None, "INR"))
    message = session._make_report()[0]["text"]
    assert message == ("Your account is in INR and I have no USD to INR rate. Type the amount in "
                       "INR, or switch accounts with /user.")


@pytest.mark.parametrize("followup", ["half now", "pay 40% now", "pay 5k now", "wait a month", "a month later",
                                       "wait 2 weeks", "6 installments", "by 2025-12-01", "actually 20k", "make it 20k"])
def test_report_followups_recompute_and_compare(followup):
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    blocks = session.handle(followup)
    assert any(block["text"].startswith(("WHAT CHANGED", "No change:")) for block in blocks)
    excluded = followup in {"half now", "pay 40% now", "pay 5k now", "wait a month", "a month later", "wait 2 weeks"}
    assert any(block["kind"] == "report" for block in blocks) is not excluded
    if excluded:
        assert blocks[0]["text"].startswith("No change:")
    assert session.latest_result is session.report


@pytest.mark.parametrize("line", ["", "???", "banana"])
def test_unrecognized_or_empty_report_followup_never_reprints_report(line):
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    blocks = session.handle(line)
    assert not any(block["kind"] == "report" for block in blocks)
    if line:
        assert "I didn't understand that." in blocks[0]["text"]
    else:
        # Empty input after a report asks for the next step instead of silence.
        assert blocks[0]["text"] == "Type a request or a follow-up."


@pytest.mark.parametrize(("command", "expected"), [("/user", "Usage: /user <id>"), ("/date", "Usage: /date YYYY-MM-DD")])
def test_user_date_usage(command, expected):
    session = TerminalSession("user_07", "2025-11-04")
    assert session.handle(command)[0]["text"] == expected


def test_user_resets_to_latest_event_date_and_date_error_has_range():
    session = TerminalSession("user_07", "2025-11-04")
    response = session.handle("/user user_05")[0]["text"]
    assert "Data range:" in response and session.as_of_date in response
    error = session.handle("/date 1900-01-01")[0]["text"]
    assert "Allowed range" in error and "through" in error


def test_default_as_of_is_day_after_latest_event():
    for user_id, expected in (("user_05", "2025-11-05"), ("user_07", "2024-09-05"),
                              ("user_20", "2026-02-07"), ("user_23", "2025-05-07")):
        session = TerminalSession(user_id)
        assert session.as_of_date == expected
        assert session.as_of_defaulted
        assert session.as_of_note == f"Using {expected}, the day after your latest bank record."
    session = TerminalSession("user_07", "2024-09-04")
    assert not session.as_of_defaulted
    assert session.as_of_note is None


def test_user_switch_uses_default_as_of_note():
    session = TerminalSession("user_07", "2024-09-05")
    response = session.handle("/user user_23")[0]["text"]
    assert response == ("User set to user_23. Data range: 2024-11-13 through 2025-05-06. "
                        "Using 2025-05-07, the day after your latest bank record.")
    assert session.as_of_date == "2025-05-07"


def test_date_before_history_blocks_advice_with_guard_message():
    session = TerminalSession("user_23", "2024-09-05")
    blocks = session.handle("Laptop 8k")
    assert blocks[0]["kind"] == "error"
    assert "No bank history for user_23 before 2024-09-05" in blocks[0]["text"]
    assert "their history runs 2024-11-13 to 2025-05-06" in blocks[0]["text"]
    assert "Use /date 2025-05-07." in blocks[0]["text"]
    assert session.phase == "ready"
    assert not session.form
    # The guard is lifted by switching to a date inside the history range.
    session.handle("/date 2025-05-07")
    blocks = session.handle("Laptop 8k")
    assert blocks[0]["text"].startswith("Got it:")


def test_not_recommended_why_has_no_unqualified_safe_claim():
    # user_07 at the default as-of: 500k cannot be paid safely by any method,
    # so "why not now" must say so instead of claiming a safe payment.
    session = TerminalSession("user_07")
    session.handle("Laptop 500000 no deadline just advise")
    assert "NOT RECOMMENDED" in render_headline(session)
    text = session.handle("why not now")[0]["text"]
    assert "No — you cannot pay the full ₹500,000 today." in text
    assert "would be safe" not in text
    assert "below your minimum" in text
    assert "You could pay up to ₹103,081 today and stay above your minimum." in text
    assert "[" not in text
    assert text.splitlines()[-1] == ("Based on: your profile, your bank history and your salary pattern "
                                     "and your employer's update EMP-0005.")


def test_why_not_now_names_the_rule_that_blocks_a_safe_payment():
    # Full payment would be safe but the profile excludes it: the profile rule
    # must be named right after the safe claim.
    session = TerminalSession("user_07")
    session.handle("Laptop 12000 no deadline just advise")
    text = session.handle("why not now")[0]["text"]
    assert "Yes — you can pay the full ₹12,000 today." in text
    assert "But you told us you only consider installments." in text
    assert "[" not in text


def test_why_not_now_names_the_deadline_rule_when_it_blocks_the_plan():
    # The interactive conflict flow usually prevents an unmet deadline; the
    # follow-up must still explain one when it reaches the report.
    session = TerminalSession("user_23", "2025-05-07")
    session.handle("Laptop 8000 no deadline just advise")
    result = session.report
    result["recommended_option"] = {"type": "installments", "safe": True,
                                    "dates": ["2025-05-07", "2025-06-06", "2025-07-06"],
                                    "amounts": {}, "meets_deadline": False,
                                    "total_cost": 8000.0, "lowest_projected_balance": 29601.86}
    session.form["pay_by_date"] = "2025-05-14"
    assert "NOT RECOMMENDED" in render_headline(session)
    text = session._why_not_now_text()
    assert "Yes — you can pay the full R8,000 today." in text
    assert "But nothing you consider completes by your deadline 2025-05-14." in text
    assert "[" not in text


def render_headline(session):
    return session.render_report(session.report).split("\n", 1)[0]


def test_installments_intake_flow_at_default_as_of():
    # The transcript flow: statement, deadline, count question, report.
    session = TerminalSession("user_07")
    blocks = session.handle("Laptop 12k")
    joined = " ".join(block["text"] for block in blocks)
    assert session.as_of_date == "2024-09-05"
    assert "Your profile only allows installments." in joined
    assert "When do you need to pay?" in blocks[-1]["text"]
    blocks = session.handle("")  # no deadline
    assert "How many installments?" in blocks[-1]["text"]
    blocks = session.handle("")  # default 3-month plan
    assert "PAY IN 3 INSTALLMENTS" in blocks[-1]["text"]


@pytest.mark.parametrize(("text", "expected"), [
    ("12k", 12_000), ("12K", 12_000), ("1.5m", 1_500_000), ("12,000", 12_000),
    ("12 000", 12_000), ("1,00,000", 100_000), ("₹1,00,000", 100_000),
    ("1.2 lakh", 120_000), ("2 cr", 20_000_000), ("1.5 mn", 1_500_000),
    ("3 crore", 30_000_000), ("Rs. 12,000", 12_000), ("$100", 100),
    ("-500", None), ("15%", None), ("0", None), ("twelve thousand", None),
])
def test_extended_amount_parsing_table(text, expected):
    assert parse_amount(text)[0] == expected


@pytest.mark.parametrize(("text", "expected"), [("12k and 5k", [12_000, 5_000]),
                                                    ("10,00,00,000", [100_000_000]),
                                                    ("1.5 bn", [1_500_000_000])])
def test_amount_candidates_table(text, expected):
    from terminal_flow import _amount_candidates
    assert _amount_candidates(text) == expected


def test_ambiguous_and_negative_amount_messages_and_large_amount_warning():
    session = TerminalSession("user_07", "2025-11-04")
    assert "Which amount did you mean: INR 12,000.00 or INR 5,000.00?" in session.handle("12k and 5k")[0]["text"]
    assert session.handle("12k")[-1]["kind"] == "prompt"
    assert session.handle("-500")[0]["text"] == "Amounts must be positive; please enter an amount above zero."
    large_session = TerminalSession("user_07", "2025-11-04")
    large_session.handle("1500 bn no deadline just advise")
    assert any("above 1,000,000,000,000" in block["text"] for block in large_session.last_blocks)


@pytest.mark.parametrize("answer", ["2025-11-03", "in 2 days", "in 2 weeks", "2026-02-10"])
def test_typed_deadline_validation_table(answer):
    session, _ = start("Laptop 12k")
    session.handle("4")
    blocks = session.handle(answer)
    if answer == "2025-11-03":
        assert session.form.get("pay_by_date") is None
        assert any("on or after" in block["text"] for block in session.last_blocks)
    elif answer == "2026-02-10":
        assert session.form.get("pay_by_date") is None
        # A too-late date is never silently dropped: the cap is stated and the
        # question is asked again.
        assert any("Deadlines can be at most 90 days ahead (until 2026-02-02)" in block["text"]
                   and "no deadline for longer installment plans" in block["text"]
                   for block in session.last_blocks)
        assert "Type a date" in blocks[-1]["text"]
    else:
        assert session.form.get("pay_by_date") in {"2025-11-06", "2025-11-18"}


@pytest.mark.parametrize(("fee", "expected", "asks_again"), [("0", 0, False), ("30", 30, False),
                                                                ("31", 0, True), ("-1", 0, True)])
def test_installment_fee_range_and_retry_table(fee, expected, asks_again):
    # The fee question is kept only when the sentence states an out-of-range fee;
    # otherwise the intake defaults to 0% without asking.
    session, _ = start("Laptop 12k no deadline in 3 installments with 31% fee")
    assert session.questions == ["fee"]
    if asks_again:
        assert session.handle(fee)[0]["kind"] == "prompt"
        session.handle(fee)
    else:
        session.handle(fee)
    assert session.form["installment_fee_pct"] == expected


def test_deadline_column_is_textual_yes_no_na():
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    values = [row["meets_deadline"] for row in session.report_data(session.report)["options"]]
    assert all(value in {"yes", "no", "n/a"} for value in values)


@pytest.mark.parametrize(("followup", "requested_now"), [("pay 5k now", 5_000), ("pay 40% now", 40_000),
                                                           ("pay half now", 50_000)])
def test_partial_followup_amount_is_used_when_safe(followup, requested_now):
    session = TerminalSession("user_07", "2024-09-04")
    session.handle("Laptop costs 100k no deadline just advise")
    blocks = session.handle(followup)
    assert not any(block["kind"] == "report" for block in blocks)
    assert blocks[0]["text"].startswith("No change:")


def test_situation_shows_actual_30_and_90_day_outflows():
    session = TerminalSession("user_07", "2024-09-04")
    text = session.handle("/situation")[0]["text"]
    situation = session.assistant.situation()
    assert f"outflows next 30 days: INR {situation['committed_outflows_next_30_days']:,.2f}" in text
    assert f"next 90 days: INR {situation['committed_outflows_next_90_days']:,.2f}" in text


def test_reported_phase_fresh_refrigerator_request():
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    assert session.phase == "reported"

    blocks = session.handle(
        "Can I buy a Refrigerator worth 50k INR by the end of next month? "
        "I will pay in full without any installments"
    )
    assert "I didn't understand that" not in blocks[0]["text"]
    assert any(b["kind"] == "report" for b in blocks)
    assert session.form["item"] == "refrigerator"
    assert session.form["amount"] == 50000.0
    assert session.form["currency"] == "INR"
    assert session.form["pay_by_date"] == "2025-12-31"  # end of next month relative to 2025-11-04
    assert session.form["allow_partial"] is False
    assert session.form["allow_installments"] is False

    # Second fresh request in reported phase
    blocks2 = session.handle("Bike 25k no deadline just advise")
    assert any(b["kind"] == "report" for b in blocks2)
    assert session.form["item"] == "bike"
    assert session.form["amount"] == 25000.0


def test_reported_phase_direct_account_questions():
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    assert session.phase == "reported"
    from money import money
    situation = session.assistant.situation()
    currency = session.assistant.engine.get_user_home_currency("user_07")
    expected_balance = money(situation["current_balance"], currency)
    capacity = max(0.0, situation["lowest_projected_balance"] - situation["minimum_balance"])
    expected_max = money(capacity, currency)

    # 1. Bank balance question only
    blocks = session.handle("What is my current bank balance?")
    assert len(blocks) == 1
    assert "I didn't understand that" not in blocks[0]["text"]
    assert expected_balance in blocks[0]["text"]

    # 2. Most expensive affordable question only
    blocks = session.handle("What is the most expensive thing I can afford right now?")
    assert len(blocks) == 1
    assert "I didn't understand that" not in blocks[0]["text"]
    assert expected_max in blocks[0]["text"]
    assert "lowest projected balance" in blocks[0]["text"]

    # 3. Combined question
    blocks = session.handle("what is my current bank balance? what is the most expensive thing I can afford right now")
    assert len(blocks) == 1
    assert "I didn't understand that" not in blocks[0]["text"]
    assert expected_balance in blocks[0]["text"]
    assert expected_max in blocks[0]["text"]


def test_existing_report_followups_continue_working():
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    assert session.phase == "reported"

    # why not now
    why_blocks = session.handle("why not now")
    assert any("balance" in b["text"].lower() or "minimum" in b["text"].lower() for b in why_blocks)

    # /why
    why_cmd_blocks = session.handle("/why")
    assert any("Data loaded:" in b["text"] for b in why_cmd_blocks)

    # pay half now
    half_blocks = session.handle("pay half now")
    assert any(b["text"].startswith("No change:") for b in half_blocks)
    assert not any(b["kind"] == "report" for b in half_blocks)

    # wait a month
    wait_blocks = session.handle("wait a month")
    assert any(b["text"].startswith("No change:") for b in wait_blocks)
    assert not any(b["kind"] == "report" for b in wait_blocks)

    # 6 installments is a genuine amendment and recomputes the plan.
    inst_blocks = session.handle("6 installments")
    assert any(b["kind"] == "report" for b in inst_blocks)

    # actually 20k
    act_blocks = session.handle("actually 20k")
    assert any(b["text"].startswith(("WHAT CHANGED", "No change:")) for b in act_blocks)
    assert any(b["kind"] == "report" for b in act_blocks)
    assert session.form["amount"] == 20000.0


def test_profile_honoured_verdict_and_useful_alternatives():
    session = TerminalSession("user_07", "2025-11-04")
    blocks = session.handle("Laptop 12k no deadline just advise")
    report_text = blocks[-1]["text"]
    # user_07's profile considers installments only, so the plain request
    # recommends the 3-installment plan instead of paying in full today.
    assert "PAY IN 3 INSTALLMENTS" in report_text

    alts_section = report_text.split("ALTERNATIVES\n", 1)[1].split("\n\nNEXT STEPS\n")[0]
    next_steps_section = report_text.split("NEXT STEPS\n", 1)[1]

    # No meaningless lines
    assert "Best other safe option: none" not in alts_section
    assert "Best other safe options: none" not in alts_section
    assert f"Earliest safe date to wait for: {session.as_of_date}" not in alts_section
    assert "Spending changes that would free cash: none" not in alts_section
    # No wait option and no pay-in-full recommendation for an installments-only profile
    assert "WAIT UNTIL" not in report_text
    assert "to pay in full and avoid fees" not in alts_section

    # Tailored next steps
    assert "Confirm the 3-installment plan" in next_steps_section
    assert "Do not make this payment now" not in next_steps_section


def test_intake_skips_redundant_partial_and_fee_when_count_stated():
    session = TerminalSession("user_07", "2024-09-04")
    blocks = session.handle("Can I pay ₹50,000 in 6 installments?")
    joined = " ".join(block["text"] for block in blocks)
    assert "Is partial payment acceptable?" not in joined
    assert "fee" not in joined.lower()
    assert session.questions == ["deadline"]
    assert session.form["allow_installments"] and session.form["installment_count"] == 6
    assert session.form["allow_partial"] is False
    assert "Got it: INR 50,000.00" in blocks[0]["text"] and "6 installments" in blocks[0]["text"]


def test_intake_asks_only_count_when_installments_already_chosen():
    session = TerminalSession("user_07", "2024-09-04")
    blocks = session.handle("Can I pay ₹50,000 in installments by 2024-11-24?")
    assert session.questions == ["installments"]
    assert "Is partial payment acceptable?" not in " ".join(b["text"] for b in blocks)
    prompt = blocks[-1]["text"]
    assert "No installments" not in prompt and "How many installments?" in prompt
    # With a deadline every count from 2 up to the cap whose plan completes in
    # time is offered — counts 2 and 3 make it; the 6-month plan does not.
    assert "1 2 months (default), 2 3 months" in prompt
    assert "6 months" not in prompt
    blocks = session.handle("")  # accepts the default: the fewest payments
    assert session.form["allow_installments"] and session.form["installment_count"] == 2
    assert any(block["kind"] == "report" for block in blocks)


def test_intake_explicit_partial_skips_installments():
    session = TerminalSession("user_07", "2024-09-04")
    session.handle("Can I pay half now for a 12k laptop?")
    assert session.questions == ["deadline"]
    assert session.form["allow_partial"] is True
    assert session.form["allow_installments"] is False
    assert session.form["partial_amount"] == 6000


def test_method_choice_controls_follow_up_questions():
    session, _ = start("Can I afford a laptop that costs 80000?")  # user_05 allows all methods
    session.handle("")  # no deadline -> method question
    blocks = session.handle("3")  # pay part now -> amount question, never a count question
    assert "How much do you want to pay now?" in blocks[-1]["text"]
    assert "installments" not in session.questions
    blocks = session.handle("")  # safest amount
    assert any(block["kind"] == "report" for block in blocks)
    assert session.form["allow_partial"] is True
    assert session.form["allow_installments"] is False

    session, _ = start("Can I afford a laptop that costs 80000?")
    session.handle("")
    blocks = session.handle("2")  # pay in full today -> straight to the report
    assert any(block["kind"] == "report" for block in blocks)
    assert "installments" not in session.questions
    assert session.form["allow_installments"] is False


def test_plain_request_still_asks_full_intake():
    session = TerminalSession("user_07", "2024-09-04")
    blocks = session.handle("Can I afford a laptop that costs 80000?")
    assert session.questions == ["deadline", "installments"]
    assert "When do you need" in blocks[-1]["text"]
    assert "Is partial payment acceptable" not in blocks[-1]["text"]


def test_acknowledgement_is_escaped_and_shown_once_per_request():
    session = TerminalSession("user_07", "2024-09-04")
    blocks = session.handle("Can I afford a <b>laptop</b> costing 80000?")
    acks = [block for block in blocks if block["text"].startswith("Got it:")]
    assert len(acks) == 1
    assert "&lt;b&gt;laptop&lt;/b&gt;" in acks[0]["text"]
    assert "<b>laptop</b>" not in acks[0]["text"]
    followup_blocks = session.handle("")  # answering a question never re-acknowledges
    assert followup_blocks and not any(block["text"].startswith("Got it:") for block in followup_blocks)


# --- Mixed currencies: everything after the request stays in the account currency ---

def test_foreign_currency_request_is_converted_once_and_shown_in_account_currency():
    session = TerminalSession("user_07", "2025-11-04")  # INR account
    blocks = session.handle("Laptop $100 no deadline just advise")
    ack = blocks[0]["text"]
    assert ack.startswith("Got it: INR 8,333.00")
    assert "$100 = ₹8,333 (rate 83.33)" in ack
    # The form is normalized to the account currency after one conversion.
    assert session.form["amount"] == 8333.0
    assert session.form["currency"] == "INR"
    report = blocks[-1]["text"]
    assert report.split("\n", 1)[0] == "╭─ PAY IN 3 INSTALLMENTS ─╮"
    # No "$" on INR balances and no "USD" label on INR amounts anywhere.
    assert "$" not in report
    assert "USD" not in report
    assert "INR 8,333.00" in report          # OPTIONS total cost
    assert "Minimum-balance level: INR 93,000.00" in report
    assert "You have ₹218,946 available today" in report


def test_foreign_currency_request_without_a_rate_keeps_the_home_amount_question():
    session = TerminalSession("user_05", "2025-11-04")  # ZAR account, no INR rate
    blocks = session.handle("Laptop Rs 100 no deadline just advise")
    ack = blocks[0]["text"]
    assert ack.startswith("Got it: INR 100.00")
    assert "=" not in ack  # nothing was converted, so no conversion line
    assert blocks[-1]["kind"] == "error"
    assert ("Your account is in ZAR and I have no INR to ZAR rate. Type the amount in "
            "ZAR, or switch accounts with /user.") == blocks[-1]["text"]


def test_foreign_currency_amendment_is_converted_too():
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    session.handle("actually $100")
    assert session.form["amount"] == 8333.0
    assert session.form["currency"] == "INR"


# --- Safe-limit boundary: exactly at the minimum is safe; no "0.00 below" contradictions ---

def test_safest_partial_amount_never_exceeds_capacity(monkeypatch):
    session = TerminalSession("user_07", "2025-11-04")
    session.form = {"amount": 50000.0, "currency": "INR", "pay_by_date": None}
    monkeypatch.setattr(session.assistant, "situation", lambda: {
        "lowest_projected_balance": 93000.0 + 1234.567, "minimum_balance": 93000.0})
    # Rounded up it would leave the balance below the minimum; floored it is safe.
    assert session._safest_partial_amount() == 1234.56


def test_boundary_partial_plan_lands_on_the_minimum_and_is_safe():
    session = TerminalSession("user_07", "2024-09-04")
    session.handle("Laptop costs 100k no deadline pay half now just advise")
    option = next(item for item in session.report["ranked_options"]
                  if item["type"] == "partial_then_remainder")
    assert option["safe"] is True
    assert option["lowest_projected_balance"] == session.report["situation"]["minimum_balance"]
    rendered = session.render_report(session.report)
    assert "0.00 below the minimum" not in rendered


def test_unsafe_note_never_claims_a_zero_shortfall():
    session = TerminalSession("user_07", "2025-11-04")
    session.handle("Laptop 12k no deadline just advise")
    result = session.report
    result["decision"]["affordability_status"] = "not_affordable"
    result["recommended_option"] = None
    minimum = result["situation"]["minimum_balance"]
    option = result["ranked_options"][0]
    option["safe"] = False
    option["lowest_projected_balance"] = minimum - 0.003  # a hair under, displays equal
    rendered = session.render_report(result)
    assert "0.00 below the minimum" not in rendered
    assert "just under the minimum" in rendered


# --- Deadline today / too short for any installment count, before the count menu ---

def test_typed_deadline_too_short_for_any_plan_reports_the_miss():
    session = TerminalSession("user_07", "2025-11-04")
    blocks = session.handle("Laptop 12k")
    session.handle("4")                   # pick a date
    blocks = session.handle("2025-11-04")  # deadline = today
    joined = " ".join(block["text"] for block in blocks)
    assert "No monthly installment plan finishes by 2025-11-04" in joined
    assert "How many installments?" not in joined
    assert "Would you like to split" not in joined  # user_07 accepts no partial payment
    assert any(block["kind"] == "report" for block in blocks)
    assert "NOT RECOMMENDED: no allowed plan completes by 2025-11-04" in blocks[-1]["text"]


def test_in_sentence_today_deadline_reports_the_miss():
    session = TerminalSession("user_07", "2025-11-04")
    blocks = session.handle("Laptop 12k pay by today")
    joined = " ".join(block["text"] for block in blocks)
    assert session.form["pay_by_date"] == "2025-11-04"
    assert "No monthly installment plan finishes by 2025-11-04" in joined
    assert "How many installments?" not in joined
    assert any(block["kind"] == "report" for block in blocks)


# --- A method the profile doesn't allow is refused with a fallback, not echoed ---

def test_profile_without_installments_refuses_named_installments():
    session = TerminalSession("user_33", "2025-11-04")  # full + partial, no installments
    blocks = session.handle("Can I pay Rs. 50,000 in 6 installments no deadline just advise")
    ack = blocks[0]["text"]
    assert ack.startswith("Got it: INR 50,000.00")
    assert "6 installments" not in ack
    joined = " ".join(block["text"] for block in blocks)
    assert "Your profile doesn't include installments, so I'll use paying in full instead." in joined
    assert session.questions == []          # no count question
    assert session.form["allow_installments"] is False
    options = session.report_data(session.report)["options"]
    assert all(row["name"] != "installments" for row in options)


def test_profile_without_installments_falls_back_to_a_safe_full_payment():
    session = TerminalSession("user_06")  # full + partial allowed, no installments
    blocks = session.handle("Laptop 200 in 6 installments no deadline just advise")
    assert "Your profile doesn't include installments, so I'll use paying in full instead." in \
        " ".join(block["text"] for block in blocks)
    assert "PAY IN FULL TODAY" in blocks[-1]["text"]


# --- Capped installments: the verdict is the capped plan when it is safe ---

def test_explicit_installments_above_cap_verdict_uses_the_capped_count():
    session = TerminalSession("user_12", "2025-11-04")  # cap 11
    blocks = session.handle("Laptop 12k in 24 installments no deadline just advise")
    joined = " ".join(block["text"] for block in blocks)
    assert "Your profile allows at most 11 installments; using 11." in joined
    assert "How many installments?" not in joined
    assert blocks[-1]["text"].split("\n", 1)[0] == "╭─ PAY IN 11 INSTALLMENTS ─╮"


def test_explicit_installments_above_cap_as_followup_keeps_the_capped_verdict():
    session = TerminalSession("user_07", "2025-11-04")  # cap 12
    session.handle("Laptop 12k no deadline just advise")
    blocks = session.handle("24 installments")
    assert any("Your profile allows at most 12 installments; using 12." in block["text"]
               for block in blocks)
    assert any(block["kind"] == "report" for block in blocks)
    assert blocks[-1]["text"].split("\n", 1)[0] == "╭─ PAY IN 12 INSTALLMENTS ─╮"
