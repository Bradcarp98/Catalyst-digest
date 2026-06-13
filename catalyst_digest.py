#!/usr/bin/env python3
"""
CATALYST: twice-weekly digital-health / medtech / startup intelligence digest.

End to end, this script:
  1. Asks Claude to research the web for recent developments.
  2. Claude decides its OWN searches, reads the results, and writes a
     categorised brief. (That self-directed loop is what makes it an agent.)
  3. The brief is wrapped in a simple HTML email and sent to you via Resend.

Run it by hand first  ->  python catalyst_digest.py
Then schedule it with cron (see the walkthrough).

Secrets live in a .env file next to this script, never in the code:
    ANTHROPIC_API_KEY=sk-ant-...
    RESEND_API_KEY=re_...
"""

import os
import sys
import datetime
import anthropic
import resend
from dotenv import load_dotenv

# --- Config (edit these) -----------------------------------------------
# Load the keys from a .env file sitting next to this script, no matter
# what folder the script is launched from (this matters for scheduled runs).
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

RECIPIENT = "brad@prac3.com.au"
SENDER = "Catalyst Digest <onboarding@resend.dev>"   # Resend's free sender; swap to your own domain later
MODEL = "claude-sonnet-4-6"                           # cheap + fast. claude-haiku-4-5-20251001 = cheaper, claude-opus-4-8 = deeper
LOOKBACK_DAYS = 4

CATEGORIES = [
    "Funding & raises",
    "New tools & launches",
    "Ideas & trends",
    "AU / APAC spotlight",
]

# --- 1. Run the research agent -----------------------------------------
def build_prompt():
    today = datetime.date.today().strftime("%d %B %Y")
    cats = ", ".join(CATEGORIES)
    return f"""Today is {today}. You are a research agent compiling a twice-weekly intelligence brief on digital health, health-tech and med-tech startups, for a clinician who follows this space closely.

Search the web for genuinely recent developments from roughly the last {LOOKBACK_DAYS} days. Cover ALL of these categories: {cats}.

Prioritise concrete signal: named startups, funding rounds (amount and lead investor), product / tool / device launches, regulatory clearances (FDA, TGA), and notable new ideas. Skip generic opinion pieces and anything clearly older than {LOOKBACK_DAYS} days. Surface Australian / APAC activity wherever it exists.

Produce the brief as an HTML fragment (no <html>, <head> or <body> tags). For each category, output:
  <h2>Category name</h2>
  then, for each item:
    <h3>Headline</h3>
    <p>One or two factual sentences.</p>
    <p class="meta">Source name &middot; Region &middot; <a href="URL">read source</a></p>
Aim for 3 to 6 items per category where the news supports it. If a category genuinely has nothing noteworthy, still output its <h2> followed by <p>Nothing significant since the last brief.</p>.

Output ONLY the HTML fragment. No preamble, no markdown code fences, no closing commentary."""


def run_agent():
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 10}],
        messages=[{"role": "user", "content": build_prompt()}],
    )
    # The response holds several block types (the searches, their results, and
    # the text Claude wrote). We keep only the text, then trim to the HTML.
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    text = text.replace("```html", "").replace("```", "").strip()
    start, end = text.find("<"), text.rfind(">")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return text


# --- 2. Wrap the fragment in a tidy email shell ------------------------
def wrap_email(fragment):
    today = datetime.date.today().strftime("%d %B %Y")
    return f"""<div style="font-family:Georgia,'Times New Roman',serif; max-width:640px; margin:0 auto; color:#1d1a17;">
  <div style="border-bottom:2px solid #e8602f; padding-bottom:12px; margin-bottom:8px;">
    <div style="font-family:monospace; font-size:11px; letter-spacing:3px; color:#e8602f; text-transform:uppercase;">Catalyst &middot; {today}</div>
    <div style="font-size:30px; font-weight:bold;">Health-Tech Intelligence Brief</div>
  </div>
  <style>
    h2 {{ font-size:13px; font-family:monospace; letter-spacing:2px; text-transform:uppercase; color:#e8602f; border-bottom:1px solid #ddd; padding-bottom:6px; margin-top:28px; }}
    h3 {{ font-size:17px; margin:18px 0 4px; }}
    p {{ font-size:15px; line-height:1.5; margin:4px 0; }}
    p.meta {{ font-family:monospace; font-size:12px; color:#888; }}
    a {{ color:#e8602f; }}
  </style>
  {fragment}
  <p style="font-family:monospace; font-size:11px; color:#aaa; margin-top:32px; border-top:1px solid #eee; padding-top:12px;">
    Compiled automatically by your Catalyst agent.
  </p>
</div>"""


# --- 3. Send it --------------------------------------------------------
def send_email(html):
    resend.api_key = os.environ["RESEND_API_KEY"]
    today = datetime.date.today().strftime("%d %b %Y")
    resend.Emails.send({
        "from": SENDER,
        "to": [RECIPIENT],
        "subject": f"Catalyst: health-tech brief, {today}",
        "html": html,
    })


def main():
    print("Researching... (give it a minute while the agent searches the web)")
    fragment = run_agent()
    if not fragment:
        print("Nothing came back; not sending an empty email.")
        sys.exit(1)
    send_email(wrap_email(fragment))
    print(f"Done. Brief sent to {RECIPIENT}.")


if __name__ == "__main__":
    main()
