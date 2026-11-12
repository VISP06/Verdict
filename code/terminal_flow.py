"""Deterministic conversational terminal flow; deliberately has no Streamlit dependency."""

import datetime as dt
import calendar
import html
import os
import re

import failproofai_sdk as fp

from assistant import Assistant
from money import money
from plan_dates import installment_completion_date


_CURRENCIES = {"INR": "INR", "USD": "USD", "EUR": "EUR", "ZAR": "ZAR", "IDR": "IDR"}
_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "R": "ZAR"}
_AMOUNT_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?|"
    r"\d{1,3}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d{1,3}(?: \d{3})+|\d+(?:\.\d+)?)(?![\d,])", re.I
)
BANNER = ("Glacier checks whether a payment fits your next 90 days: your balance, income, bills and pending items, "
          "and tells you whether to pay now, split it, or wait.")


def parse_amount(text):
    """Return the first positive amount and explicit currency, if supplied."""
    value = str(text or "")
    currency = None
    for code in _CURRENCIES:
        if re.search(rf"\b{code}\b", value, re.I):
            currency = code
            break
    for symbol, code in _SYMBOLS.items():
        found = (symbol in value if symbol != "R" else bool(re.search(r"(?<!\w)R(?=\s*\d)", value, re.I)))
        if found:
            currency = code
            break
    if re.search(r"(?<!\w)rs\.?(?!\w)|\brupees?\b", value, re.I):
        currency = "INR"
    amount_text = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b", " ", value)
    amount_text = re.sub(
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:\s+20\d{2})?\b", " ", amount_text, flags=re.I,
    )
    amount_text = re.sub(r"\bwithin\s+\d+\s+days\b", " ", amount_text, flags=re.I)
    matches = [m for m in _AMOUNT_RE.finditer(amount_text)
               if not re.match(r"\s*%", amount_text[m.end():])
               and not re.search(r"-\s*$", amount_text[:m.start()])]
    # Prefer explicit monetary notation and grouped/decimal amounts over stray
    # dates, installment counts, or other plain integers in a question.
    def priority(match):
        raw = match.group(0)
        prefix = amount_text[max(0, match.start() - 8):match.start()]
        suffix = amount_text[match.end():match.end() + 8]
        explicit = (bool(re.search(r"(?:INR|USD|EUR|ZAR|IDR|Rs\.?|₹|\$|€|R)\s*$", prefix, re.I))
                    or bool(re.match(r"\s*(?:INR|USD|EUR|ZAR|IDR)\b", suffix, re.I)))
        marked = bool(re.search(r"[, ]|\.", raw)) or bool(re.match(r"\s*(?:k|m|mn|mm|b|bn|million|lakh|lakhs|lac|lacs|cr|crore|crores)\b", suffix, re.I))
        return (0 if explicit else 1 if marked else 2, match.start())
    for match in sorted(matches, key=priority):
        raw = match.group(0).replace(",", "").replace(" ", "")
        try:
            amount = float(raw)
        except ValueError:
            continue
        suffix = amount_text[match.end():]
        prefix = amount_text[:match.start()]
        unit = re.match(r"\s*(k|m|mn|mm|b|bn|million|lakh|lakhs|lac|lacs|cr|crore|crores)(?![a-z])", suffix, re.I)
        if unit:
            u = unit.group(1).lower()
            amount *= (1_000 if u == "k" else 1_000_000 if u in {"m", "mn", "mm", "million"}
                       else 1_000_000_000 if u in {"b", "bn"}
                       else 100_000 if u in {"lakh", "lakhs", "lac", "lacs"} else 10_000_000)
        elif re.search(r"(?:\d)\s*$", prefix) and " " in prefix[-2:]:
            continue
        if amount > 0:
            return amount, currency
    return None, currency


_FOLLOWUP_COUNT_PATTERNS = (
    re.compile(r"\b(\d+)\s*installments?\b"),
    re.compile(r"\binstallments?\b[^.?!]*?\b(?:to|be|into)\s+(\d+)\b"),
    re.compile(r"\buse\s+(\d+)\s+instead\b"),
    re.compile(r"\bsplit\s+(?:it\s+)?into\s+(\d+)\b"),
)
_PURCHASE_CUE_RE = re.compile(r"\b(?:pay|paying|afford|cost|costs|buy|buying|purchase)\b")
_CURRENCY_RE = re.compile(r"(?:₹|\$|€|\b(?:INR|USD|EUR|ZAR|IDR|rs\.?)\b|(?<!\w)R(?=\s*\d))", re.I)


def _followup_count(low):
    """Installment count stated by an amendment such as 'make it 3 installments'."""
    for pattern in _FOLLOWUP_COUNT_PATTERNS:
        match = pattern.search(low)
        if match:
            return int(match.group(1))
    return None


def _is_installment_count_followup(low):
    """True when the sentence only amends the installment count of the prior request."""
    return (_followup_count(low) is not None
            and not _PURCHASE_CUE_RE.search(low)
            and not _CURRENCY_RE.search(low))


def _amount_candidates(text):
    """Extract distinct amounts so ambiguous requests can be clarified."""
    value = str(text or "")
    result = []
    for match in _AMOUNT_RE.finditer(value):
        if re.match(r"\s*%", value[match.end():]):
            continue
        prefix = value[max(0, match.start() - 8):match.start()]
        suffix = value[match.end():match.end() + 12]
        explicit = bool(re.search(r"(?:INR|USD|EUR|ZAR|IDR|Rs\.?|₹|\$|€|R)\s*$", prefix, re.I))
        unit = bool(re.match(r"\s*(?:k|m|mn|mm|b|bn|lakh|lakhs|lac|lacs|cr|crore|crores)\b", suffix, re.I))
        grouped = "," in match.group(0) or " " in match.group(0) or "." in match.group(0)
        if not (explicit or unit or grouped):
            continue
        amount, _ = parse_amount(value[match.start():match.end() + 12])
        if amount is not None and amount not in result:
            result.append(amount)
    return result


class TerminalSession:
    def __init__(self, user_id="user_07", as_of_date=None, dataset_dir=None, assistant=None):
        self.dataset_dir = dataset_dir or os.path.join(os.path.dirname(__file__), "..", "dataset")
        self.assistant = assistant
        if assistant is None:
            assistant = Assistant(user_id, as_of_date or self._latest_date(user_id),
                                  dataset_dir=self.dataset_dir, no_llm=True)
        self.assistant = assistant
        self.user_id = str(user_id)
        self.as_of_date = str(as_of_date or assistant.as_of_date)[:10]
        if self.user_id not in self.assistant.engine.profiles.index.astype(str):
            raise ValueError(f"Unknown user id: {self.user_id}")
        self.run_id = assistant.run_id
        self.session_id = f"chat-{self.run_id}"
        self.phase = "ready"
        self.request_text = ""
        self.form = {}
        self.questions = []
        self.question_index = 0
        self.invalid_attempts = 0
        self.report = None
        self.latest_result = None
        self.latest_wording = "plain text"
        self.last_blocks = []
        self._installment_count_given = False
        self._installment_count_only = False
        self._conflict_reasks_installments = False

    def _earliest_installment_completion(self):
        return installment_completion_date(self.as_of_date, min(self.installment_counts))

    @property
    def installment_counts(self):
        cap = self.assistant._max_installment_months()
        counts = [count for count in (3, 6, 12) if cap is None or count <= cap]
        if not counts:
            counts = [cap]
        return tuple(counts)

    def _eligible_installment_counts(self):
        deadline = self.form.get("pay_by_date")
        if not deadline:
            return list(self.installment_counts)
        return [count for count in self.installment_counts
                if installment_completion_date(self.as_of_date, count) <= dt.date.fromisoformat(str(deadline)[:10])]

    def _installment_options_text(self):
        counts = self._eligible_installment_counts()
        if not counts:
            counts = list(self.installment_counts)
        if getattr(self, "_installment_count_only", False):
            return "1 {} months (default)".format(counts[0]) + "".join(f", {idx + 2} {count} months" for idx, count in enumerate(counts[1:]))
        return "1 no (default)" + "".join(f", {idx + 2} {count} months" for idx, count in enumerate(counts))

    def _installment_deadline_message(self, deadline):
        earliest = installment_completion_date(self.as_of_date, min(self.installment_counts))
        return (f"No installment option completes by {deadline}; the shortest plan ends "
                f"on {earliest}, after the deadline.")

    def _has_installment_conflict(self, deadline):
        count = self.form.get("installment_count") if (self.form.get("allow_installments") and getattr(self, "_installment_count_given", False)) else None
        return bool(count and deadline and installment_completion_date(self.as_of_date, count) > dt.date.fromisoformat(str(deadline)[:10]))

    def _latest_date(self, user_id):
        import pandas as pd
        engine = Assistant(user_id, dt.date.today().isoformat(), dataset_dir=self.dataset_dir, no_llm=True).engine
        dates = pd.to_datetime(engine.events[engine.events.user_id.astype(str).eq(str(user_id))].event_date, errors="coerce").dropna()
        return dates.max().date().isoformat() if not dates.empty else dt.date.today().isoformat()

    def _block(self, kind, text, data=None):
        block = {"kind": kind, "text": str(text), "plain_text": str(text), "data": data or {}}
        self.last_blocks.append(block)
        return block

    def _prompt(self):
        question = self.questions[self.question_index]
        count_only = question == "installments" and getattr(self, "_installment_count_only", False)
        if question == "deadline_conflict":
            earliest = installment_completion_date(self.as_of_date, self.form["installment_count"])
            text = f"The selected installment plan ends {earliest}, after that deadline. Choose 1 none, or type a date on or after {earliest}."
            low = earliest if isinstance(earliest, str) else earliest.isoformat()
            high = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
            options = [{"key": "1", "label": "Remove the deadline", "default": True}]
            if "full_payment" in self.assistant._profile_allowed_methods():
                options.append({"key": "2", "label": "Pay in full today instead", "default": False})
            return self._block("prompt", text, {"question": f"That installment plan would finish on {low}, after your deadline. What would you like to do?",
                                                "options": options,
                                                "picker": {"label": "Or choose a later date", "min": low, "max": max(low, high)}})
        if question == "date":
            text = "Type a date in YYYY-MM-DD format."
            low = self.as_of_date
            high = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
            return self._block("prompt", text, {"question": "Which date do you need to pay by?", "options": [], "picker": {"label": "Choose a date", "min": low, "max": high}})
        options = {
            "deadline": "When do you need to pay? [1 none (default), 2 within 14 days, 3 end of this month, 4 type YYYY-MM-DD]",
            "partial": "Is partial payment acceptable? [1 no (default), 2 yes]",
            "installments": f"{'How many installments?' if count_only else 'Use installments?'} [{self._installment_options_text()}]",
            "fee": "Optional installment fee percentage? [Enter for 0%]",
        }
        text = options[question]
        clean_questions = {"deadline": "When do you need to pay?", "partial": "Is partial payment acceptable?",
                           "installments": "How many installments?" if count_only else "Would you like to pay in installments?",
                           "fee": "Is there a fee for installments?"}
        if question == "deadline": choices = [("1", "No deadline", True), ("2", "Within 14 days", False), ("3", "By the end of this month", False), ("4", "Pick a date…", False)]
        elif question == "partial": choices = [("1", "No, pay it in full", True), ("2", "Yes, part now and the rest later", False)]
        elif question == "installments":
            counts = self._eligible_installment_counts()
            if not counts:
                counts = list(self.installment_counts)
            if getattr(self, "_installment_count_only", False):
                choices = [(str(idx + 1), f"{count} monthly payments", idx == 0) for idx, count in enumerate(counts)]
            else:
                choices = [("1", "No installments", True)] + [(str(idx + 2), f"{count} monthly payments", False) for idx, count in enumerate(counts)]
        else: choices = [(str(value), "No fee" if value == 0 else f"{value}% fee", value == 0) for value in (0, 1, 2, 5)]
        return self._block("prompt", text, {"question": clean_questions.get(question, text), "options": [{"key": k, "label": label, "default": default} for k, label, default in choices]})

    def _start(self, line):
        amount, currency = parse_amount(line)
        candidates = _amount_candidates(line)
        if len(candidates) > 1 and not hasattr(self, "_forced_amount"):
            self.phase = "amount_clarification"
            self._ambiguous_line = line
            self._amount_choices = candidates
            options = " or ".join(money(item, currency or self.assistant.engine.get_user_home_currency(self.user_id)) for item in candidates)
            prompt = f"Which amount did you mean: {options}?"
            return [self._block("prompt", prompt, {"question": prompt, "options": []})]
        if hasattr(self, "_forced_amount"):
            amount = self._forced_amount
            del self._forced_amount
        if amount is None:
            if re.search(r"(?<!\w)-\s*\d", line):
                return [self._block("error", "Amounts must be positive; please enter a non-negative amount.")]
            text = "What amount are you considering?"
            return [self._block("prompt", text, {"question": text, "options": []})]
        self.request_text = line
        home = str(self.assistant.engine.get_user_home_currency(self.user_id))
        self.form = {"amount": amount, "currency": currency or home,
                     "item": self._request_item(line),
                     "allow_partial": False, "allow_installments": False,
                     "installment_count": 3, "installment_fee_pct": 0.0}
        lowered = line.lower()
        self.questions = []
        self._installment_count_given = False
        self._installment_count_only = False
        if not self._parse_deadline(line):
            self.questions.append("deadline")
        partial = re.search(r"pay half now|partial payment|part now", lowered)
        count = re.search(r"\b(\d+)\s+installments?\b", lowered)
        declined = re.search(r"\b(?:no installments?|without (?:any )?installments?|without installments?)\b", lowered)
        chosen = bool(count) or bool(not declined and re.search(r"\b(?:installments?|instalments?)\b|\bin\s+parts\b", lowered))
        fee_match = re.search(r"(\d+(?:\.\d+)?)\s*%", lowered)
        if re.search(r"\b(no partial|pay in full|full payment only|in full)\b", lowered):
            self.form["partial_declined"] = True
        if partial and not chosen:
            self.form["allow_partial"] = True
            self.form["allow_installments"] = False
            if "pay half now" in lowered:
                self.form["partial_amount"] = amount * 0.5
        elif not re.search(r"\b(no partial|pay in full|full payment only|in full)\b", lowered) and not chosen:
            self.questions.append("partial")
        if count:
            self.form.update(allow_installments=True, installment_count=int(count.group(1)),
                             allow_partial=False)
            self._installment_count_given = True
            cap = self.assistant._max_installment_months()
            if cap and self.form["installment_count"] > cap:
                self.form["installment_count"] = cap
                self._block("text", f"Your profile allows at most {cap} installments; using {cap}.")
            if fee_match:
                fee = float(fee_match.group(1))
                if 0 <= fee <= 30: self.form["installment_fee_pct"] = fee
                else: self.questions.append("fee")
        elif declined:
            self.form["allow_installments"] = False
            self.form["installments_declined"] = True
        elif chosen:
            self.form.update(allow_installments=True, allow_partial=False)
            self._installment_count_only = True
            self.questions.append("installments")
            if fee_match and not (0 <= float(fee_match.group(1)) <= 30):
                self.questions.append("fee")
        elif partial:
            pass  # explicit partial request excludes the installment plan entirely
        else:
            self.questions.append("installments")
        self._acknowledge(lowered)
        if self.form.get("allow_installments") and self.form.get("pay_by_date") and self._has_installment_conflict(self.form["pay_by_date"]):
            deadline = self.form["pay_by_date"]
            self.questions = [q for q in self.questions if q != "deadline"]
            self.questions.insert(0, "deadline_conflict")
            self._block("text", f"The selected {self.form['installment_count']}-installment plan conflicts with the deadline {deadline}.")
        self.phase = "intake"
        self.question_index = 0
        if re.search(r"\bjust advise\b", lowered):
            self.questions = []
        if self.form.get("pay_by_date") and not self._eligible_installment_counts() and "installments" in self.questions:
            # No allowed installment plan completes by the deadline; ask the same
            # conflict question as when a chosen count misses the deadline.
            index = self.questions.index("installments")
            self.questions[index] = "deadline_conflict"
            self.form["installment_count"] = min(self.installment_counts)
            self._conflict_reasks_installments = True
            self._block("text", self._installment_deadline_message(self.form["pay_by_date"]))
        if amount > 1_000_000_000_000:
            self._block("text", "Warning: that amount is above 1,000,000,000,000; please check it carefully.")
        if self.questions:
            prompt_block = self._prompt()
            return self.last_blocks
        return self._make_report()

    @staticmethod
    def _request_item(text):
        """Extract the purchase name from common amount-and-deadline phrasing."""
        value = str(text or "").strip()
        match = re.search(
            r"\b(?:buy|purchase)\s+(?:a|an|the)?\s*(.+?)\s+"
            r"(?:worth|costing|costs?|priced at)\s+(?:₹|\$|€|R\s*)?"
            r"[\d,.]+\s*(?:k|thousand|lakh|lakhs|lac|lacs|crore|crores)?",
            value, re.I,
        )
        if match:
            item = match.group(1)
        else:
            item = re.sub(r"\b(?:can i|can we|should i|should we)\b", "", value, flags=re.I)
            item = re.sub(r"\b(?:buy|purchase|afford)\b", "", item, flags=re.I)
            item = re.split(r"\b(?:worth|costing|costs?|by|i will|i'll|without|no installments?)\b", item, maxsplit=1, flags=re.I)[0]
            item = re.sub(r"(?:₹|\$|€|\b(?:INR|USD|EUR|ZAR|IDR|Rs\.?)\b)\s*", "", item, flags=re.I)
            item = re.sub(r"\b\d[\d,.]*(?:\s*(?:k|thousand|lakh|lakhs|lac|lacs|crore|crores))?\b", "", item, flags=re.I)
            item = re.sub(r"\b(?:no deadline|no due date|just advise|in full|pay in full|pay half now)\b", "", item, flags=re.I)
        item = re.sub(r"\b(?:a|an|the)\b", "", item, flags=re.I)
        return re.sub(r"\s+", " ", item).strip(" ?.!,$") .lower() or "payment"

    def _parse_deadline(self, text):
        if re.search(r"\b(no deadline|no due date|just advise)\b", text, re.I):
            self.form["pay_by_date"] = None
            return True
        iso = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
        if iso:
            try:
                parsed = dt.date.fromisoformat(iso.group(1))
                today = dt.date.fromisoformat(self.as_of_date)
                if parsed >= today:
                    self.form["pay_by_date"] = parsed.isoformat()
                    return True
            except ValueError:
                pass
        relative = re.search(r"\bin\s+(\d+)\s+(days?|weeks?)\b", text, re.I)
        if relative:
            n = int(relative.group(1)); unit = relative.group(2).lower()
            self.form["pay_by_date"] = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=n * (7 if unit.startswith("week") else 1))).isoformat()
            return True
        if re.search(r"\bwithin 14 days\b", text, re.I):
            self.form["pay_by_date"] = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=14)).isoformat()
            return True
        if re.search(r"\bend of this month\b", text, re.I):
            day = dt.date.fromisoformat(self.as_of_date)
            next_month = day.replace(day=28) + dt.timedelta(days=4)
            self.form["pay_by_date"] = (next_month.replace(day=1) - dt.timedelta(days=1)).isoformat()
            return True
        if re.search(r"\b(?:end of next month|by the end of next month)\b", text, re.I):
            day = dt.date.fromisoformat(self.as_of_date)
            first_next = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            first_following = (first_next.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            self.form["pay_by_date"] = (first_following - dt.timedelta(days=1)).isoformat()
            return True
        return False

    def _acknowledge(self, lowered):
        """One short acknowledgement per new request, built only from parsed fields."""
        parts = [f"Got it: {money(self.form['amount'], self.form['currency'])}"]
        item = str(self.form.get("item") or "")
        if item and item != "payment" and "installments" not in item:
            parts.append(html.escape(item, quote=True))
        if self.form.get("allow_installments") and self._installment_count_given:
            parts.append(f"{int(self.form['installment_count'])} installments")
            if self.form.get("installment_fee_pct"):
                parts.append(f"{self.form['installment_fee_pct']:g}% fee")
        elif self.form.get("allow_installments"):
            parts.append("installments")
        elif self.form.get("allow_partial"):
            parts.append("half now" if self.form.get("partial_amount") else "partial payment")
        if self.form.get("pay_by_date"):
            parts.append(f"pay by {self.form['pay_by_date']}")
        elif re.search(r"\b(?:no deadline|no due date|just advise)\b", lowered):
            parts.append("no deadline")
        else:
            parts.append("no deadline given yet")
        self._block("text", " · ".join(parts) + ".")

    def _accept_answer(self, line):
        key = self.questions[self.question_index]
        value = line.strip()
        dynamic_counts = self._eligible_installment_counts() or list(self.installment_counts)
        installment_choices = {str(idx + 2): count for idx, count in enumerate(dynamic_counts)}
        count_only = key == "installments" and getattr(self, "_installment_count_only", False)
        if count_only:
            installment_choices = {str(idx + 1): count for idx, count in enumerate(dynamic_counts)}
        conflict_choices = {"1": None}
        if "full_payment" in self.assistant._profile_allowed_methods():
            conflict_choices["2"] = "full"
        choices = {"deadline": {"1": None, "2": "14", "3": "month", "4": "date"},
                   "deadline_conflict": conflict_choices, "partial": {"1": False, "2": True},
                   "installments": (installment_choices if count_only else {"1": False, **installment_choices})}
        is_date = key == "date" or (key == "deadline_conflict" and value not in {"", "1"}
                                    and not (value == "2" and "2" in conflict_choices))
        valid = value == "" or (key in choices and value in choices[key]) or (key == "fee" and bool(re.fullmatch(r"\d+(?:\.\d+)?", value)) and 0 <= float(value) <= 30)
        relative_value = re.fullmatch(r"in\s+(\d+)\s+(days?|weeks?)", value, re.I)
        if relative_value and key in {"date", "deadline_conflict"}:
            n = int(relative_value.group(1)); unit = relative_value.group(2).lower()
            parsed_relative = dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=n * (7 if unit.startswith("week") else 1))
            value = parsed_relative.isoformat()
            is_date = True
        if is_date and value:
            try:
                parsed_date = dt.date.fromisoformat(value)
                as_of = dt.date.fromisoformat(self.as_of_date)
                valid = parsed_date >= as_of
                if valid and parsed_date > as_of + dt.timedelta(days=90):
                    valid = True
                if key == "deadline_conflict":
                    valid = parsed_date >= installment_completion_date(self.as_of_date, self.form["installment_count"])
            except ValueError:
                valid = False
        if not valid:
            self.invalid_attempts += 1
            if self.invalid_attempts == 1:
                if key in {"date", "deadline_conflict"}:
                    self._block("text", f"Date must be on or after {self.as_of_date} and within the 90-day forecast.")
                return [self._prompt()]
            value = ""
        self.invalid_attempts = 0
        if key in {"deadline", "deadline_conflict"}:
            if key == "deadline_conflict":
                if value in {"", "1"}:
                    self.form["pay_by_date"] = None
                elif value == "2":
                    # The user takes the option that meets the deadline: pay in
                    # full today. The kept deadline stays untouched.
                    self.form["allow_installments"] = False
                    self.form["installments_declined"] = True
                    self._installment_count_only = False
                else:
                    self.form["pay_by_date"] = dt.date.fromisoformat(value).isoformat()
            elif value == "2":
                self.form["pay_by_date"] = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=14)).isoformat()
            elif value == "3":
                d = dt.date.fromisoformat(self.as_of_date); n = d.replace(day=28) + dt.timedelta(days=4)
                self.form["pay_by_date"] = (n.replace(day=1) - dt.timedelta(days=1)).isoformat()
            elif value == "4":
                self.questions[self.question_index] = "date"
                return [self._prompt()]
            else:
                self.form["pay_by_date"] = None
        elif key == "date":
            parsed = dt.date.fromisoformat(value) if value else None
            if parsed and parsed > dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90):
                self.form["pay_by_date"] = None
                self._block("text", "That date is beyond the 90-day forecast; I'll show the earliest safe date instead.")
            else:
                self.form["pay_by_date"] = parsed.isoformat() if parsed else None
        elif key == "partial":
            self.form["allow_partial"] = value == "2"
            if value != "2":
                self.form["partial_declined"] = True
            if value == "2" and "installments" in self.questions[self.question_index + 1:]:
                self.questions.remove("installments")
                self.form["allow_installments"] = False
        elif key == "installments":
            self.form["allow_installments"] = True if count_only else bool(value and value != "1")
            if count_only:
                self.form["installment_count"] = installment_choices.get(value, installment_choices["1"])
            elif value and value != "1":
                self.form["installment_count"] = choices[key][value]
            else:
                self.form["installments_declined"] = True
            self._installment_count_given = bool(self.form["allow_installments"])
            self._installment_count_only = False
        elif key == "fee": self.form["installment_fee_pct"] = float(value) if value else 0.0
        self.question_index += 1
        blocks = []
        if key == "deadline_conflict" and getattr(self, "_conflict_reasks_installments", False):
            # The conflict question replaced a pending installments question;
            # once the deadline is resolved the count still needs an answer.
            self._conflict_reasks_installments = False
            if value != "2" and "installments" not in self.questions[self.question_index:]:
                self.questions.insert(self.question_index, "installments")
        if key in {"deadline", "deadline_conflict"} and self.form.get("pay_by_date") and self.form.get("allow_installments") and self._has_installment_conflict(self.form["pay_by_date"]):
            conflict_index = max(0, self.question_index - 1)
            self.questions[conflict_index] = "deadline_conflict"
            self.question_index = conflict_index
            self._block("text", f"The selected installment plan conflicts with the deadline {self.form['pay_by_date']}.")
            self._prompt()
            return self.last_blocks
        if key in {"deadline", "deadline_conflict"} and self.form.get("pay_by_date") and not self._eligible_installment_counts() and "installments" in self.questions[self.question_index:]:
            index = self.questions.index("installments", self.question_index)
            self.questions[index] = "deadline_conflict"
            self.form["installment_count"] = min(self.installment_counts)
            self._conflict_reasks_installments = True
            blocks.append(self._block("text", self._installment_deadline_message(self.form["pay_by_date"])))
        if self.question_index < len(self.questions):
            blocks.append(self._prompt())
            return blocks
        return self._make_report()

    def _make_report(self):
        self.phase = "reported"
        self.form.setdefault("pay_by_date", None)
        try:
            home = str(self.assistant.engine.get_user_home_currency(self.user_id))
            converted, _ = self.assistant._home_amount(self.form["amount"], self.form["currency"])
            if converted is None:
                return [self._block("error", f"Your account is in {home} and I have no {self.form['currency']} to {home} rate. Type the amount in {home}, or switch accounts with /user.")]
            with fp.session(self.session_id):
                with fp.agent("assistant", goal="Produce deterministic payment advice"):
                    with fp.tool_call("advise", input={"user_id": self.user_id, "amount": self.form["amount"]}) as tool:
                        result = self.assistant.advise(self.form)
                        tool.output = result
            self.report = result
            self.latest_result = result
            self.latest_wording = "plain text"
            text = self.render_report(result)
            return [self._block("report", text, self.report_data(result))]
        except Exception as exc:
            fp.event.error(session_id=self.session_id, agent_id="assistant", error_type=type(exc).__name__, message=str(exc)[:300])
            return [self._block("error", f"Unable to produce advice: {exc}")]

    def _unmet_deadline(self, result):
        """True when the recommended installment plan finishes after the kept deadline."""
        chosen = result.get("recommended_option") or {}
        deadline = self.form.get("pay_by_date")
        return bool(deadline and chosen.get("type") == "installments"
                    and chosen.get("meets_deadline") is False)

    def render_report(self, result):
        currency = self.form["currency"]
        options = result.get("ranked_options", [])
        chosen = result.get("recommended_option")
        if chosen is not None and not chosen.get("safe", False):
            chosen = None
        not_affordable = str(result.get("decision", {}).get("affordability_status")) == "not_affordable"
        unmet_deadline = self._unmet_deadline(result)
        if unmet_deadline:
            verdict = f"NOT RECOMMENDED: no allowed plan completes by {self.form['pay_by_date']}"
        elif not_affordable or chosen is None:
            verdict = "NOT RECOMMENDED: no option keeps your balance above the minimum"
        elif chosen["type"] == "pay_in_full_now": verdict = "PAY IN FULL TODAY"
        elif chosen["type"] == "installments": verdict = f"PAY IN {self.form.get('installment_count', 3)} INSTALLMENTS"
        elif chosen["type"] == "partial_then_remainder": verdict = f"PAY {money(next(iter(chosen['amounts'].values())), currency)} NOW AND THE REST ON {list(chosen['amounts'])[-1]}"
        elif chosen["type"] == "wait": verdict = f"WAIT UNTIL {max(chosen['dates'])}"
        else: verdict = "NOT RECOMMENDED"
        situation = result["situation"]
        why = (f"The current balance is {money(situation['current_balance'], currency)}, and the minimum to keep is {money(situation['minimum_balance'], currency)}. "
               f"The lowest projected balance is {money(situation['lowest_projected_balance'], currency)} on {situation['lowest_projected_date']}. "
               f"Monthly counted income is {money(situation['monthly_income_total'], currency)}; only validated recurring income is counted.")
        pending = situation.get("pending_items_considered_or_ignored", {})
        why += (f" Pending items: considered {', '.join(map(str, pending.get('considered', []))) or 'none'}; "
                f"ignored {', '.join(map(str, pending.get('ignored', []))) or 'none'}.")
        balances = result.get("evidence", {}).get("balances", {})
        vals = list(balances.values())
        levels = "▁▂▃▄▅▆▇█"
        spark = "" if not vals else "".join(levels[round((v-min(vals))/(max(vals)-min(vals))*7)] if max(vals) != min(vals) else "▄" for v in vals)
        minimum = situation["minimum_balance"]
        rows = ["OPTION                 WHEN                 TOTAL COST       LOWEST BALANCE   MEETS DEADLINE   SAFE"]
        for option in options:
            safe_mark = "✓" if option.get("safe") else "✗"
            safety = "SAFE" if option.get("safe") else (
                f"(drops to {money(option['lowest_projected_balance'],currency)} on "
                f"{option['lowest_projected_date']}, {money(max(0, minimum-option['lowest_projected_balance']),currency)} below the minimum)"
            )
            meets = option.get("meets_deadline")
            meets = "n/a" if meets is None else ("yes" if meets else "no")
            rows.append(f"{option['type'].replace('_',' '):22} {', '.join(option['dates']):20} {money(option['total_cost'],currency):16} {money(option['lowest_projected_balance'],currency):16} {meets}   {safe_mark} {'SAFE' if option.get('safe') else 'UNSAFE'}")
            if not option.get("safe"):
                rows.append(f"                         Note: {safety}")
        allowed_types = {{"full_payment": "pay_in_full_now", "partial_payment": "partial_then_remainder",
                          "installments": "installments", "wait": "wait"}.get(method)
                         for method in self.assistant._profile_allowed_methods()}
        other_safe_items = [x for x in options if x.get("safe") and x is not chosen
                            and x.get("type") in allowed_types]
        other_safe_names = [x["type"].replace("_", " ") for x in other_safe_items]
        earliest = situation.get("earliest_date_for_full_payment")
        safe_today = float(result["decision"].get("amount_safe_to_pay", 0) or 0)
        spending_changes = result["decision"].get("spending_changes_needed", "none")
        shortest_plan = installment_completion_date(self.as_of_date, min(self.installment_counts))
        alts = []

        if unmet_deadline:
            alts.append(
                f"No payment method you would consider completes by {self.form['pay_by_date']}; "
                f"the shortest allowed plan ends on {shortest_plan}, after the deadline.")
            alts.append(f"Remove the deadline or pick a date on or after {shortest_plan} to see a recommended plan.")
            if other_safe_names:
                alts.append(f"Safe alternative options: {', '.join(other_safe_names)}.")
        elif verdict.startswith("PAY IN FULL TODAY"):
            if other_safe_names:
                alts.append(f"Other safe payment structures: {', '.join(other_safe_names)}.")
            else:
                alts.append("Paying in full today is your most direct and cost-effective safe option.")
            buffer = situation["lowest_projected_balance"] - minimum
            alts.append(
                f"Projected lowest balance stays {money(buffer, currency)} above your required minimum "
                f"on {situation['lowest_projected_date']}."
            )
        elif verdict.startswith("NOT RECOMMENDED"):
            if safe_today > 0:
                alts.append(f"Amount safe to pay today: {money(safe_today, currency)} (downsized purchase).")
            if earliest and earliest > self.as_of_date:
                alts.append(f"Earliest safe date to wait for: {earliest}.")
            if other_safe_names:
                alts.append(f"Safe alternative options: {', '.join(other_safe_names)}.")
            if spending_changes != "none":
                alts.append(f"Spending changes that would free cash: {spending_changes}.")
            if not alts:
                alts.append("No safe alternative within the 90-day forecast. Consider reducing the amount or deferring.")
        elif "WAIT" in verdict:
            if safe_today > 0:
                alts.append(f"Amount safe to pay today: {money(safe_today, currency)} (partial payment).")
            if other_safe_names:
                alts.append(f"Other safe options: {', '.join(other_safe_names)}.")
            if spending_changes != "none":
                alts.append(f"Spending changes to enable paying sooner: {spending_changes}.")
            if not alts:
                alts.append(f"Waiting until {earliest} avoids dropping below your required minimum balance.")
        else:
            if ("full_payment" in self.assistant._profile_allowed_methods()
                    and earliest and earliest > self.as_of_date):
                alts.append(f"Full payment alternative: wait until {earliest} to pay in full and avoid fees.")
            if other_safe_names:
                alts.append(f"Other safe options: {', '.join(other_safe_names)}.")
            if spending_changes != "none":
                alts.append(f"Spending changes that would free cash: {spending_changes}.")
            if not alts:
                alts.append("The selected plan is the earliest way to proceed safely without overdraft risk.")

        if unmet_deadline:
            steps = [
                f"Do not start this plan: nothing you would consider completes by {self.form['pay_by_date']}.",
                f"Pick a deadline on or after {shortest_plan}, or remove the deadline, then ask again.",
                "This answer could change if you move the deadline or choose fewer installments.",
            ]
        elif verdict.startswith("NOT RECOMMENDED"):
            steps = ["Do not make this payment now"]
            if safe_today > 0:
                steps.append(f"Amount safe to pay today: {money(safe_today, currency)}.")
            if earliest:
                steps.append(f"Earliest safe date: {earliest}.")
            steps.append(
                "This answer could change if income resumes, you choose a smaller amount, "
                f"or spending changes ({spending_changes})."
            )
        elif verdict.startswith("PAY IN FULL TODAY"):
            steps = [
                f"Confirm the selected plan for {money(self.form['amount'], currency)}.",
                f"Keep the minimum balance of {money(minimum, currency)} available in your account.",
                "Recheck the forecast before making the payment if unexpected expenses occur.",
            ]
        elif "WAIT" in verdict:
            chosen_date = max(chosen["dates"]) if chosen and chosen.get("dates") else (earliest or "the safe date")
            steps = [
                f"Hold the payment until {chosen_date}; do not pay in full today.",
                f"Ensure minimum balance of {money(minimum, currency)} remains protected.",
                "Recheck your balance before the safe date to ensure expected income arrived.",
            ]
        elif "INSTALLMENTS" in verdict:
            count = self.form.get("installment_count", 3)
            steps = [
                f"Confirm the {count}-installment plan for {money(self.form['amount'], currency)}.",
                f"Ensure sufficient funds on each monthly due date while maintaining {money(minimum, currency)}.",
                "Recheck the forecast before making each scheduled payment.",
            ]
        else:
            steps = [
                f"Confirm the selected plan for {money(self.form['amount'], currency)}.",
                f"Keep the minimum balance of {money(minimum, currency)} available.",
                "Recheck the forecast before making the payment.",
            ]
        dates = list(balances)
        start_date = dates[0] if dates else self.as_of_date
        end_date = dates[-1] if dates else (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
        start_balance = vals[0] if vals else situation["current_balance"]
        end_balance = vals[-1] if vals else situation["current_balance"]
        return (f"╭─ {verdict} ─╮\nWHY\n{why}\n\nNUMBERS\n90-day balance: {spark}\nMinimum-balance level: {money(minimum,currency)}\n{start_date} {money(start_balance,currency)} → {end_date} {money(end_balance,currency)}\n\nOPTIONS\n" + "\n".join(rows) + "\n\nALTERNATIVES\n" + "\n".join("• "+s for s in alts) + "\n\nNEXT STEPS\n" + "\n".join("• "+s for s in steps) + "\n╰" + "─" * max(10, len(verdict) + 4) + "╯")

    def report_data(self, result):
        rendered = self.render_report(result)
        verdict = rendered.split("╭─ ", 1)[1].split(" ─╮", 1)[0]
        chosen = result.get("recommended_option")
        if self._unmet_deadline(result):
            status = "bad"
        elif not_affordable := (str(result.get("decision", {}).get("affordability_status")) == "not_affordable"):
            status = "bad"
        elif chosen is None or not chosen.get("safe", False):
            status = "bad"
        elif chosen.get("type") == "wait":
            status = "caution"
        else:
            status = "good"
        situation = result["situation"]
        balances = result.get("evidence", {}).get("balances", {})
        values = list(balances.values())
        dates = list(balances)
        options = []
        for item in result.get("ranked_options", []):
            low = item.get("lowest_projected_balance")
            options.append({"name": item.get("type", "").replace("_", " "), "when": ", ".join(item.get("dates", [])),
                            "cost": money(item.get("total_cost", 0), self.form["currency"]),
                            "lowest": money(low, self.form["currency"]),
                            "meets_deadline": "n/a" if item.get("meets_deadline") is None else ("yes" if item.get("meets_deadline") else "no"),
                            "safe": bool(item.get("safe")),
                            "note": "" if item.get("safe") else f"Unsafe: drops to {money(low, self.form['currency'])} on {item.get('lowest_projected_date')}"})
        sections = rendered.split("\n\nALTERNATIVES\n")
        alternatives = sections[-1].split("\n\nNEXT STEPS\n")[0].splitlines() if len(sections) > 1 else []
        next_steps = rendered.split("\n\nNEXT STEPS\n", 1)[-1].split("\n╰", 1)[0].splitlines()
        return {"verdict": verdict, "status": status,
                "why": rendered.split("WHY\n", 1)[-1].split("\n\nNUMBERS", 1)[0],
                "numbers": {"sparkline_values": values, "minimum": situation["minimum_balance"],
                            "start_date": dates[0] if dates else self.as_of_date,
                            "end_date": dates[-1] if dates else self.as_of_date,
                            "start_balance": values[0] if values else situation["current_balance"],
                            "end_balance": values[-1] if values else situation["current_balance"]},
                "options": options, "alternatives": alternatives, "next_steps": next_steps}

    def _handle_account_inquiry(self, text):
        low = text.lower()
        is_balance = bool(re.search(
            r"\b(?:bank\s+)?balance\b|\bhow\s+much\s+(?:do\s+i\s+have|money\s+(?:do\s+i\s+have|in\s+(?:my\s+)?account))\b",
            low,
        ))
        is_afford = bool(re.search(
            r"\b(?:most\s+expensive(?:\s+thing)?|how\s+much\s+can\s+i\s+(?:afford|spend)|what\s+(?:is\s+the\s+most\s+expensive\s+thing\s+)?(?:i\s+can\s+afford|can\s+i\s+afford)|afford\s+right\s+now|maximum\s+(?:purchase|amount|payment))\b",
            low,
        ))
        if not (is_balance or is_afford):
            return None
        situation = self.assistant.situation()
        currency = str(self.assistant.engine.get_user_home_currency(self.user_id))
        curr_bal = situation["current_balance"]
        min_bal = situation["minimum_balance"]
        low_bal = situation["lowest_projected_balance"]
        low_date = situation["lowest_projected_date"]
        capacity = max(0.0, low_bal - min_bal)
        if is_balance and is_afford:
            resp = (
                f"Your current available balance is {money(curr_bal, currency)}. "
                f"The most expensive payment you can safely afford right now is {money(capacity, currency)}. "
                f"Over the next 90 days, your lowest projected balance is {money(low_bal, currency)} on {low_date}, "
                f"keeping you at or above your required minimum balance of {money(min_bal, currency)}."
            )
        elif is_balance:
            resp = (
                f"Your current available balance is {money(curr_bal, currency)} "
                f"(minimum balance requirement: {money(min_bal, currency)})."
            )
        else:
            resp = (
                f"The most expensive payment you can safely afford right now is {money(capacity, currency)}. "
                f"Over the next 90 days, your lowest projected balance is {money(low_bal, currency)} on {low_date}, "
                f"leaving a safe margin above your required minimum balance of {money(min_bal, currency)}."
            )
        self._block("text", resp)
        return self.last_blocks

    def _is_new_request_in_reported_phase(self, text):
        amount, _ = parse_amount(text)
        if amount is None:
            return False
        low = text.lower().strip()
        if re.search(r"\b(actually|make it)\b", low):
            return False
        if re.search(r"\bpay\s+(?:\d+(?:\.\d+)?%|[\d,.]+(?:\s*(?:k|thousand))?)\s+now\b", low):
            return False
        if re.match(r"^(?:wait\b|a month later|half now|pay half now)", low):
            return False
        if re.fullmatch(r"(?:3|6|12)\s+installments?", low):
            return False
        if re.fullmatch(r"by\s+20\d\d-\d\d-\d\d", low):
            return False
        if _is_installment_count_followup(low):
            # "change number of installments to 3" & co. amend the prior request.
            return False
        if (self.report is not None and not _CURRENCY_RE.search(low)
                and not _PURCHASE_CUE_RE.search(low)
                and re.search(r"\b(?:to|be|into)\s+\d", low)):
            # A bare number after to/be/into with no currency and no purchase cue
            # is never a new amount for a fresh request.
            return False
        return True

    def handle(self, line):
        self.last_blocks = []
        text = str(line or "").strip()
        command = text.lower()
        if command.startswith("/help"):
            self._block("text", "Start with an amount and optional currency. Intake asks only unanswered questions; Enter accepts defaults and /skip uses defaults. Commands: /user <id>, /users [currency], /date YYYY-MM-DD, /situation, /why, /reset, /export, /help, /skip. Use /export after a report to get plain text.")
        elif command == "/why":
            self._block("text", self._why_text())
        elif command == "/users" or command.startswith("/users "):
            requested = text.split(maxsplit=1)[1].upper() if len(text.split()) > 1 else None
            profiles = self.assistant.engine.profiles
            if requested:
                profiles = profiles[profiles.home_currency.astype(str).str.upper().eq(requested)]
            lines = [f"{uid} · {row.home_currency} · {money(row.current_available_balance, row.home_currency)}"
                     for uid, row in profiles.head(8).iterrows()]
            self._block("text", "Accounts" + (f" in {requested}" if requested else "") + ":\n" + ("\n".join(lines) or "No matching accounts."))
        elif command == "/export":
            if self.report:
                self._block("export", self.render_report(self.report))
            else:
                self._block("text", "No report to export yet. Ask an amount first.")
        elif command == "/situation":
            s = self.assistant.situation(); c = self.assistant.engine.get_user_home_currency(self.user_id)
            outflows = s.get("committed_outflows_next_30_days", s.get("outflows_next_30_days", s.get("outflows_next_30", 0)))
            outflows90 = s.get("committed_outflows_next_90_days", s.get("outflows_next_90_days", s.get("outflows_next_90", 0)))
            pending = s.get("pending_items", s.get("pending_items_considered_or_ignored", {}))
            self._block("text", (f"Balance: {money(s['current_balance'],c)}; minimum: {money(s['minimum_balance'],c)}; "
                                  f"lowest projected balance: {money(s['lowest_projected_balance'],c)} on {s.get('lowest_projected_date', 'n/a')}; "
                                  f"income counted: {money(s.get('monthly_income_total', 0),c)}; "
                                  f"outflows next 30 days: {money(outflows,c)}; next 90 days: {money(outflows90,c)}; "
                                  f"pending items: {pending}; as of {self.as_of_date}."))
        elif command == "/reset":
            self.phase = "ready"; self.form = {}; self.questions = []; self.report = None
            self._block("text", "Session reset. What are you considering?")
        elif command == "/user": self._block("text", "Usage: /user <id>")
        elif command.startswith("/user "):
            new_user = text.split(maxsplit=1)[1]
            if new_user not in self.assistant.engine.profiles.index.astype(str): self._block("error", f"Unknown user id: {new_user}")
            else:
                self.__init__(new_user, None, self.dataset_dir)
                dates = sorted(self.assistant.engine.events[self.assistant.engine.events.user_id.astype(str).eq(new_user)].event_date.dropna().astype(str))
                self._block("text", f"User set to {new_user}. Data range: {dates[0][:10]} through {dates[-1][:10]}; as of {self.as_of_date}.")
        elif command == "/date": self._block("text", "Usage: /user <id>")
        elif command.startswith("/date "):
            value = text.split(maxsplit=1)[1]
            all_dates = self.assistant.engine.events[self.assistant.engine.events.user_id.astype(str).eq(self.user_id)].event_date.dropna().astype(str)
            lo, hi = sorted(map(str, all_dates))[0][:10], sorted(map(str, all_dates))[-1][:10]
            try:
                date = dt.date.fromisoformat(value)
                if not lo <= value <= hi: raise ValueError
                self.__init__(self.user_id, date.isoformat(), self.dataset_dir)
                self._block("text", f"As-of date set to {value}.")
            except Exception: self._block("error", f"Invalid date. Allowed range for {self.user_id}: {lo} through {hi}.")
        elif self.phase == "intake" and command == "/skip":
            self.question_index = len(self.questions)
            self._make_report()
        elif self.phase == "amount_clarification":
            amount, _ = parse_amount(text)
            if amount not in getattr(self, "_amount_choices", []):
                self._block("prompt", f"Which amount did you mean: {' or '.join(money(x, self.assistant.engine.get_user_home_currency(self.user_id)) for x in self._amount_choices)}?")
            else:
                self.phase = "ready"
                self._forced_amount = amount
                self._start(self._ambiguous_line)
        elif self.phase == "intake":
            new_amount, _ = parse_amount(text)
            is_answer = text in {"", "1", "2", "3", "4"} or (self.question_index < len(self.questions) and self.questions[self.question_index] == "fee" and bool(re.fullmatch(r"\d+(?:\.\d+)?", text)))
            if (self.question_index >= len(self.questions) or self.questions[self.question_index] != "fee") and re.search(r"(?<!\w)-\s*\d", text):
                self._block("error", "Amounts must be positive; please enter a non-negative amount.")
            elif new_amount is not None and not is_answer:
                self._block("text", "Starting a new request and resetting the current intake.")
                self.phase = "ready"
                self.form = {}; self.questions = []; self.question_index = 0
                self._start(text)
            else:
                self._accept_answer(text)
        elif self.phase == "reported":
            if re.search(r"\bwhy\b|\bnot now\b", text, re.I) and self.report:
                report_text = self.render_report(self.report)
                why = report_text.split("WHY\n", 1)[-1].split("\n\nNUMBERS", 1)[0]
                self._block("text", why)
            elif (account_inquiry := self._handle_account_inquiry(text)) is not None:
                return account_inquiry
            elif self._is_new_request_in_reported_phase(text):
                self.phase = "ready"
                self.form = {}
                self.questions = []
                self.question_index = 0
                self.invalid_attempts = 0
                self.report = None
                return self._start(text)
            else:
                previous = self._comparison(self.report) if self.report else None
                changed = self._apply_followup(text)
                if changed and self._has_installment_conflict(self.form.get("pay_by_date")):
                    # The follow-up kept the deadline but its plan no longer
                    # completes by it: resolve with the first flow's conflict
                    # question instead of recommending a late plan.
                    self.phase = "intake"
                    self.questions = ["deadline_conflict"]
                    self.question_index = 0
                    self._conflict_reasks_installments = False
                    self._block("text", f"The selected {self.form['installment_count']}-installment plan conflicts with the deadline {self.form['pay_by_date']}.")
                    self._prompt()
                elif changed:
                    blocks = self._make_report()
                    if self.report and previous:
                        comparison = self._block("text", "WHAT CHANGED\n" + self._comparison(self.report, previous))
                        self.last_blocks.remove(comparison)
                        self.last_blocks.insert(0, comparison)
                elif text:
                    self._block("text", "I didn't understand that. Try: 'pay half now', 'wait a month', '6 installments', 'actually 20k', or /help.")
        else:
            self._start(text)
        return self.last_blocks

    def _comparison(self, result, previous=None):
        result = result or {}
        current = {
            "verdict": self.report_data(result).get("verdict", "n/a"),
            "amount safe today": money(result.get("decision", {}).get("amount_safe_to_pay", 0), self.form.get("currency", "INR")),
            "lowest balance": money(result.get("situation", {}).get("lowest_projected_balance", 0), self.form.get("currency", "INR")),
        }
        if previous is None:
            return "\n".join(f"{key}: {value}" for key, value in current.items())
        old = {
            "verdict": self._previous_verdict,
            "amount safe today": self._previous_safe,
            "lowest balance": self._previous_low,
        }
        return "\n".join(f"{key}: {old[key]} → {value}" for key, value in current.items())

    def _apply_followup(self, text):
        value = str(text or "").strip()
        if not value:
            return False
        low = value.lower()
        # Preserve the old report values before updating the shared form.
        self._previous_verdict = self.report_data(self.report).get("verdict", "n/a")
        self._previous_safe = money(self.report.get("decision", {}).get("amount_safe_to_pay", 0), self.form.get("currency", "INR"))
        self._previous_low = money(self.report.get("situation", {}).get("lowest_projected_balance", 0), self.form.get("currency", "INR"))
        if "half" in low or re.search(r"\bpay\s+\d+(?:\.\d+)?\s*%\s+now\b", low) or re.search(r"\bpay\s+[\d,.]+\s*(?:k|thousand)?\s+now\b", low):
            self.form["allow_partial"] = True
            self.form["allow_installments"] = False
            percent = re.search(r"\bpay\s+(\d+(?:\.\d+)?)\s*%\s+now\b", low)
            explicit = re.search(r"\bpay\s+([\d,.]+)\s*(k|thousand)?\s+now\b", low)
            if percent:
                self.form["partial_amount"] = self.form["amount"] * float(percent.group(1)) / 100
            elif explicit:
                raw = explicit.group(1) + (explicit.group(2) or "")
                partial, partial_currency = parse_amount(raw)
                if partial is not None:
                    home_amount, _ = self.assistant._home_amount(partial, partial_currency or self.form["currency"])
                    request_home, _ = self.assistant._home_amount(self.form["amount"], self.form["currency"])
                    self.form["partial_amount"] = (self.form["amount"] * home_amount / request_home
                                                    if home_amount is not None and request_home else partial)
            elif "half" in low:
                self.form["partial_amount"] = self.form["amount"] * 0.5
            return True
        count = _followup_count(low)
        if count:
            # Amending the installment count keeps the same amount, item and
            # deadline; it never re-asks intake questions.
            self.form.update(allow_installments=True, installment_count=count, allow_partial=False)
            self.form.pop("installments_declined", None)
            self._installment_count_given = True
            self._installment_count_only = False
            cap = self.assistant._max_installment_months()
            if cap and self.form["installment_count"] > cap:
                self.form["installment_count"] = cap
                self._block("text", f"Your profile allows at most {cap} installments; using {cap}.")
            return True
        if re.search(r"\bwait\b|\ba month later\b", low):
            date = dt.date.fromisoformat(self.as_of_date)
            amount = re.search(r"\b(\d+)\s*(days?|weeks?|months?)\b", low)
            if amount:
                n, unit = int(amount.group(1)), amount.group(2)
                if unit.startswith("day"): date += dt.timedelta(days=n)
                elif unit.startswith("week"): date += dt.timedelta(weeks=n)
                else:
                    month = date.month - 1 + n
                    date = date.replace(year=date.year + month // 12, month=month % 12 + 1, day=min(date.day, calendar.monthrange(date.year + month // 12, month % 12 + 1)[1]))
            elif "month" in low: date = date.replace(year=date.year + (date.month == 12), month=date.month % 12 + 1, day=min(date.day, calendar.monthrange(date.year + (date.month == 12), date.month % 12 + 1)[1]))
            else: return False
            self.form["pay_by_date"] = date.isoformat()
            self.form["allow_partial"] = False
            self.form["allow_installments"] = False
            return True
        iso = re.search(r"\bby\s+(20\d\d-\d\d-\d\d)\b", low)
        if iso:
            self.form["pay_by_date"] = iso.group(1)
            return True
        new_amount, currency = parse_amount(value)
        if new_amount is not None and re.search(r"\b(actually|make it)\b", low):
            self.form["amount"] = new_amount
            if currency: self.form["currency"] = currency
            return True
        return False

    def _why_text(self):
        result = self.latest_result
        if not result:
            return "No completed answer yet. Ask an amount first."
        start = dt.date.fromisoformat(self.as_of_date)
        events = self.assistant.engine.events
        user_events = events[events.user_id.astype(str).eq(self.user_id)]
        evidence = result.get("evidence", {})
        balance_dates = sorted(evidence.get("balances", {}))
        window_start = balance_dates[0] if balance_dates else start.isoformat()
        window_end = balance_dates[-1] if balance_dates else (start + dt.timedelta(days=90)).isoformat()
        breakdown = evidence.get("breakdown", {})
        decisions = result.get("income_stream_classes", [])
        if not decisions:
            decisions = breakdown.get("income_decisions", [])
        if not decisions:
            decisions = evidence.get("explanation_evidence", {}).get("income_decisions", [])
        income_lines = [f"{entry.get('series_key', 'unknown stream')}: {entry.get('decision', 'unknown')} — {entry.get('reason', 'reason unavailable')}"
                        for entry in decisions]
        messages = evidence.get("retrieved_message_ids", [])
        if not messages:
            messages = evidence.get("explanation_evidence", {}).get("retrieved_message_ids", [])
        option_names = [option.get("type", "unknown") for option in result.get("ranked_options", [])]
        return (f"Data loaded: {len(user_events)} events for {self.user_id}; as-of {self.as_of_date}; "
                f"90-day window {window_start} through {window_end}.\n"
                f"Income streams/classes: {'; '.join(income_lines) if income_lines else 'No stream-class detail in engine evidence.'}\n"
                f"Retrieved message IDs: {', '.join(map(str, messages)) if messages else 'none in engine evidence.'}\n"
                f"Options considered: {', '.join(option_names) if option_names else 'none'}.\n"
                f"Wording: {self.latest_wording}.\nFailproof session: {self.session_id}")
