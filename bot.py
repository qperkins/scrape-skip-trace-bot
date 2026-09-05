"""Slack bot for Harris County tax sale scraping and skip tracing."""

import os
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_bolt.adapter.socket_mode import SocketModeHandler

from email_results import EmailError, is_email_configured, send_results_email
from pipeline import (
    save_csv,
    scrape_all_combined,
    scrape_hctax_only,
    scrape_lgbs_only,
)
from skip_trace import SkipTraceError, skip_trace_dataframe

load_dotenv()

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

bolt_app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)
handler = SlackRequestHandler(bolt_app)

flask_app = Flask(__name__)


def _format_runtime(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _should_skip_trace(text: str) -> bool:
    return "skip-trace" in text.lower() or "skiptrace" in text.lower()


def _run_job(
    *,
    client,
    channel_id: str,
    user_id: str,
    label: str,
    fetch_df,
    do_skip_trace: bool,
) -> None:
    started = time.time()
    try:
        df = fetch_df()
        matching_rows = df.attrs.get("matching_rows")
        unique_matching = df.attrs.get("unique_matching_addresses")
        notice = df.attrs.get("notice")
        fallback_source = df.attrs.get("fallback_source")

        if do_skip_trace:
            df, trace_stats = skip_trace_dataframe(df)
            suffix = "skip_traced"
            title = label
            if fallback_source:
                title = f"{fallback_source} (fallback)"
            summary_lines = [
                f"✅ *{title}* complete (skip traced)",
                f"• Total properties: {trace_stats['total_properties']}",
                f"• Successfully traced: {trace_stats['successfully_traced']}",
                f"• Credits used: {trace_stats['credits_used']}",
            ]
        else:
            suffix = "scraped"
            title = fallback_source or label
            summary_lines = [
                f"✅ *{title}* complete",
                f"• Total properties: {len(df)}",
            ]
            if matching_rows is not None:
                summary_lines[1] = f"• Matching addresses (both sources): {matching_rows}"
                if unique_matching is not None and unique_matching != matching_rows:
                    summary_lines.append(f"• Unique addresses: {unique_matching}")

        if notice:
            summary_lines.insert(1, f"⚠️ {notice}")

        summary_lines.append(f"• Runtime: {_format_runtime(time.time() - started)}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{label.lower().replace(' ', '_')}_{suffix}_{timestamp}.csv"
        csv_path = save_csv(df, OUTPUT_DIR / filename)

        summary_text = "\n".join(summary_lines)

        if is_email_configured():
            try:
                recipient = send_results_email(
                    csv_path=csv_path,
                    subject=f"Scrape results: {label} ({suffix})",
                    body=summary_text.replace("*", ""),
                )
                summary_lines.append(f"• Emailed to {recipient}")
            except EmailError as exc:
                summary_lines.append(f"• Email failed: {exc}")

        client.chat_postMessage(
            channel=channel_id,
            text="\n".join(summary_lines),
        )
        client.files_upload_v2(
            channel=channel_id,
            file=str(csv_path),
            title=filename,
            initial_comment=f"Results for <@{user_id}>",
        )
    except SkipTraceError as exc:
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ Skip trace failed for *{label}*: {exc}",
        )
    except Exception as exc:
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ Job failed for *{label}*: {exc}",
        )


def _start_job(body, client, *, label: str, fetch_df, do_skip_trace: bool) -> None:
    channel_id = body["channel_id"]
    user_id = body["user_id"]
    action = "Scraping and skip tracing" if do_skip_trace else "Scraping"

    def run() -> None:
        client.chat_postMessage(
            channel=channel_id,
            text=f"⏳ {action} *{label}*... I'll post results here when done.",
        )
        _run_job(
            client=client,
            channel_id=channel_id,
            user_id=user_id,
            label=label,
            fetch_df=fetch_df,
            do_skip_trace=do_skip_trace,
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()


@bolt_app.command("/scrape-lgbs")
def scrape_lgbs(ack, body, client, command):
    ack()
    do_skip_trace = _should_skip_trace(command.get("text", ""))
    _start_job(body, client, label="LGBS", fetch_df=scrape_lgbs_only, do_skip_trace=do_skip_trace)


@bolt_app.command("/scrape-hctax")
def scrape_hctax(ack, body, client, command):
    ack()
    do_skip_trace = _should_skip_trace(command.get("text", ""))
    _start_job(body, client, label="HCTax", fetch_df=scrape_hctax_only, do_skip_trace=do_skip_trace)


@bolt_app.command("/scrape-all")
def scrape_all(ack, body, client, command):
    ack()
    do_skip_trace = _should_skip_trace(command.get("text", ""))
    _start_job(body, client, label="Matching", fetch_df=scrape_all_combined, do_skip_trace=do_skip_trace)


@flask_app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)


def main():
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if app_token:
        print("Starting in Socket Mode (local dev)...")
        SocketModeHandler(bolt_app, app_token).start()
    else:
        port = int(os.environ.get("PORT", 3000))
        print(f"Starting HTTP server on port {port}...")
        flask_app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
