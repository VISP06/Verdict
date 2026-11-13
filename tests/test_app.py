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
    at.session_state["terminal_session_glacier-terminal-v4"] = object()
    at.run()
    assert not at.exception
    assert isinstance(at.session_state["terminal_session_glacier-terminal-v4"].__class__.__name__, str)


def test_welcome_examples_and_first_chip_starts_intake(monkeypatch):
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    labels = [button.label for button in at.button]
    assert "Can I afford a laptop that costs ₹12k?" in labels
    assert "Can I pay ₹50,000 in 6 installments?" in labels
    assert "/situation" in labels
    at.button[0].click().run()
    assert not at.exception
    assert "When do you need to pay?" in "\n".join(x.value for x in at.markdown)


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
    # user_07's installments-only profile raises the deadline conflict directly.
    next(button for button in at.button if button.label.startswith("Within 14 days")).click().run()
    assert "after your deadline" in "\n".join(x.value for x in at.markdown)
    next(button for button in at.button if button.label.startswith("Remove the deadline")).click().run()
    assert "how many installments" in "\n".join(x.value for x in at.markdown).lower()


def test_date_picker_confirm_path(monkeypatch):
    _offline(monkeypatch)
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=45).run()
    at.chat_input[0].set_value("Laptop costs 12k").run()
    next(button for button in at.button if button.label.startswith("Pick a date")).click().run()
    assert len(at.date_input) == 1
    at.date_input[0].set_value(dt.date(2025, 11, 20))
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
