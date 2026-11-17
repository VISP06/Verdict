import os
import datetime as dt

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(__file__))


def test_terminal_page_and_three_default_answers_produce_report(monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    assert not at.exception
    at.chat_input[0].set_value("Can I afford this laptop which costs 12k").run()
    assert not at.exception
    assert "When do you need to pay?" in "\n".join(x.value for x in at.markdown)
    for _ in range(3):
        at.chat_input[0].set_value(" ").run()
    assert not at.exception
    assert "WHY" in "\n".join(x.value for x in at.markdown)


def test_stale_session_state_is_rebuilt(monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.session_state["terminal_session_verdict-terminal-v5"] = object()
    at.run()
    assert not at.exception
    assert isinstance(at.session_state["terminal_session_verdict-terminal-v5"].__class__.__name__, str)


def test_welcome_examples_and_first_chip_starts_intake(monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    labels = [button.label for button in at.button]
    md = "\n".join(x.value for x in at.markdown)
    assert "See how a purchase fits your next 90 days." in md
    assert "Can I afford a laptop that costs ₹12k?" in labels
    assert "Can I pay ₹50,000 in 6 installments?" in labels
    assert "Show my current situation" in labels
    at.button[0].click().run()
    assert not at.exception
    assert "When do you need to pay?" in "\n".join(x.value for x in at.markdown)


def test_situation_example_renders_a_structured_snapshot(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    next(button for button in at.button if button.label == "Show my current situation").click().run()
    assert not at.exception
    markdown = "\n".join(item.value for item in at.markdown)
    assert 'class="situation-card"' in markdown
    assert "ACCOUNT SNAPSHOT" in markdown
    assert "lowest projected balance:" not in markdown
    assert "pending items: {" not in markdown
    assert "Show my current situation" in markdown
    assert "Account currency:" not in markdown


def test_finished_report_status_and_user_text_are_safe(monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("Laptop costs 500k no deadline just advise").run()
    assert not at.exception
    markdown = "\n".join(x.value for x in at.markdown)
    assert "status-good" in markdown or "status-caution" in markdown or "status-bad" in markdown
    assert "None" not in markdown
    assert 'class="unsafe-note"' in markdown
    at.chat_input[0].set_value("<script>alert(1)</script>").run()
    markdown = "\n".join(x.value for x in at.markdown)
    assert "&lt;script&gt;" in markdown
    assert "<script>alert(1)</script>" not in markdown


def _offline(monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_clicking_deadline_option_advances_intake(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("Laptop costs 12k").run()
    # user_07's installments-only profile: no monthly plan finishes by that
    # deadline, so the report explains the miss instead of asking a conflict.
    next(button for button in at.button if button.label.startswith("Within 14 days")).click().run()
    markdown = "\n".join(x.value for x in at.markdown)
    assert "No monthly installment plan finishes by" in markdown
    assert "NOT RECOMMENDED" in markdown


def test_date_picker_confirm_path(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("Laptop costs 12k").run()
    # The deadline prompt now carries the calendar directly; no "Pick a date…"
    # intermediate question any more.
    assert len(at.date_input) == 1
    at.date_input[0].set_value(dt.date(2024, 11, 20))
    next(button for button in at.button if button.label == "Confirm date").click().run()
    assert not at.exception
    assert "installments" in "\n".join(x.value for x in at.markdown).lower()


def test_report_followup_chip_recomputes_report(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("Laptop costs 12k no deadline just advise").run()
    assert not any(button.label == "Pay half now" for button in at.button)
    next(button for button in at.button if button.label.startswith("Use 6 installments")).click().run()
    assert not at.exception
    rendered = "\n".join(x.value for x in at.markdown)
    assert "No change:" in rendered or "WHAT CHANGED" in rendered
    assert "Download report" in [button.label for button in at.button] or len(at.get("download_button")) > 0


def test_export_command_has_download_and_helpful_empty_state(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("/export").run()
    assert "No report to export yet" in "\n".join(x.value for x in at.markdown)
    at.chat_input[0].set_value("Laptop costs 12k no deadline just advise").run()
    at.chat_input[0].set_value("/export").run()
    assert len(at.get("download_button")) >= 2


def _click(at, prefix):
    next(button for button in at.button if button.label.startswith(prefix)).click().run()


def test_options_are_plain_language_without_numbers_or_bracket_lists(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("Laptop costs 12k").run()
    labels = [button.label for button in at.button if button.key and button.key.startswith("prompt_")]
    assert labels, "prompt options should render as buttons"
    assert not any(label[:1].isdigit() for label in labels)
    html = "\n".join(x.value for x in at.markdown)
    assert "When do you need to pay?" in html
    assert "[1 none" not in html and "(default)" not in html


def test_full_conversation_with_clicks_only_and_choice_echoed(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    at.chat_input[0].set_value("Laptop costs 12k").run()
    _click(at, "No deadline")
    _click(at, "3 monthly payments")
    html = "\n".join(x.value for x in at.markdown)
    # The interactive path honours the profile: user_07 considers installments
    # only, so declining installments leaves no method the profile allows and
    # the deterministic report is NOT RECOMMENDED.
    assert "PAY IN 3 INSTALLMENTS" in html
    assert "No deadline" in html and "3 monthly payments" in html
    assert "\u276f 1<" not in html


def test_answered_prompts_render_disabled_and_keep_their_choice(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    at.chat_input[0].set_value("Laptop costs 12k").run()
    live = [b.key for b in at.button if b.key and b.key.startswith("prompt_")]
    assert live and not any(k.endswith("_done") for k in live)
    first_id = {k[len("prompt_"):].rsplit("_", 1)[0] for k in live}
    assert len(first_id) == 1               # one prompt -> one id across its widgets
    _click(at, "No deadline")
    keys = [b.key for b in at.button if b.key and b.key.startswith("prompt_")]
    done = [k for k in keys if k.endswith("_done")]
    live = [k for k in keys if not k.endswith("_done")]
    assert done and live
    # The answered prompt keeps its widgets read-only under the same id...
    assert {k[len("prompt_"):-len("_done")].rsplit("_", 1)[0] for k in done} == first_id
    # ...and the newest prompt is interactive under a fresh unique id, so a
    # re-run can never resubmit an older prompt's value.
    assert {k[len("prompt_"):].rsplit("_", 1)[0] for k in live}.isdisjoint(first_id)


def test_stale_bare_numbers_after_a_report_leave_the_report_intact(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    at.chat_input[0].set_value("Laptop costs 12k").run()
    _click(at, "No deadline")
    _click(at, "3 monthly payments")
    md = "\n".join(x.value for x in at.markdown)
    assert "PAY IN 3 INSTALLMENTS" in md       # the report is on the table
    for click in ("1", "2", "1", "2"):
        at.chat_input[0].set_value(click).run()
        md = "\n".join(x.value for x in at.markdown)
        assert "That choice is no longer active. Ask a new question or use one of the suggestions." in md
        assert "PAY IN 3 INSTALLMENTS" in md   # ...and stays there
        assert "Got it: ₹1.00" not in md       # no phantom new request


def test_options_table_scrolls_horizontally_on_narrow_screens(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("Laptop costs 500k no deadline just advise").run()
    md = "\n".join(x.value for x in at.markdown)
    assert '<div class="options-scroll"><table class="options-table">' in md
    assert "white-space:nowrap" in md          # headers never wrap
    assert "overflow-x:auto" in md


def test_user_selectbox_switches_account_resets_and_defaults_as_of(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    assert not at.exception
    assert len(at.selectbox) == 1 and at.selectbox[0].value == "user_07"
    md = "\n".join(x.value for x in at.markdown)
    # Profile summary sits under the always-visible picker in the main area.
    assert "Currency INR · Balance ₹2,18,945.56 · Minimum ₹93,000.00 · Accepts: installments (up to 12)" in md
    # An intake in progress is thrown away by the switch.
    at.chat_input[0].set_value("Laptop costs 12k").run()
    session_key = "terminal_session_verdict-terminal-v5"
    assert at.session_state[session_key].phase == "intake"
    at.selectbox[0].set_value("user_20").run()
    assert not at.exception
    assert at.session_state["terminal_user"] == "user_20"
    session = at.session_state[session_key]
    assert session.user_id == "user_20"
    assert session.phase == "ready" and not session.form
    assert session.as_of_date == "2026-02-07" and session.as_of_defaulted
    md = "\n".join(x.value for x in at.markdown)
    assert ("User set to user_20. Data range: 2025-08-13 through 2026-02-06. "
             "Using 2026-02-07, the date of your request.") in md
    assert '<span class="chip"><span class="label">user </span>user_20</span>' in md
    assert "Accepts: full, partial, installments (up to 11)" in md


def test_user_line_variants_switch_account_and_sync_the_picker(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    at.chat_input[0].set_value("/user USER_23").run()
    assert not at.exception
    assert at.session_state["terminal_user"] == "user_23"
    assert at.selectbox[0].value == "user_23"   # the picker follows the command
    md = "\n".join(x.value for x in at.markdown)
    assert ("User set to user_23. Data range: 2024-11-13 through 2025-05-06. "
             "Using 2025-05-07, the date of your request.") in md
    at.chat_input[0].set_value("/user banana").run()
    md = "\n".join(x.value for x in at.markdown)
    assert "Unknown user id: banana. Closest: user_01, user_02, user_03." in md
    assert at.session_state["terminal_user"] == "user_23"  # unknown id never switches
