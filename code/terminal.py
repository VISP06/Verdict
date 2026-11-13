"""Plain terminal interface for the deterministic payment advisor."""

import argparse

from terminal_flow import BANNER, TerminalSession


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="user_07")
    parser.add_argument("--date", dest="as_of_date")
    args = parser.parse_args(argv)
    try:
        session = TerminalSession(args.user, args.as_of_date)
    except ValueError as exc:
        print(f"\033[91m{exc}\033[0m")
        return 2
    currency = str(session.assistant.engine.get_user_home_currency(session.user_id))
    print(BANNER)
    print(f"Account currency: {currency}. Amounts without a currency are treated as {currency}.")
    note = session.as_of_note
    if note:
        print(note)
    try:
        while True:
            try: line = input("\033[96m>\033[0m ")
            except EOFError: break
            if line.strip().lower() in {"quit", "exit"}: break
            for block in session.handle(line):
                color = "91" if block["kind"] == "error" else "96" if block["kind"] == "prompt" else "97"
                print(f"\033[{color}m{block['plain_text']}\033[0m")
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
