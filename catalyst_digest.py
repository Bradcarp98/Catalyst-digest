#!/usr/bin/env python3
"""
CATALYST: weekly digital-health / medtech / startup intelligence digest.

End to end, this script:
  1. Loads the record of what previous briefs have already covered.
  2. Asks Claude to research the web for recent developments, explicitly
     excluding anything on that already-covered list.
  3. Claude decides its OWN searches, reads the results, and returns the
     items as structured JSON. (That self-directed loop is what makes it
     an agent.)
  4. New items are rendered into the HTML email and sent via Resend, then
     appended to the history file so the next brief will not repeat them.

Run it by hand ->  python catalyst_digest.py
Scheduled runs happen on GitHub Actions (.github/workflows/digest.yml),
which also commits the updated history file back to the repo.

Secrets live in a .env file next to this script, never in the code:
    ANTHROPIC_API_KEY=sk-ant-...
    RESEND_API_KEY=re_...
"""

import os
import re
import sys
import json
import html
import datetime

import anthropic
import resend
from dotenv import load_dotenv

# --- Config (edit these) -----------------------------------------------
# Load the keys from a .env file sitting next to this script, no matter
# what folder the script is launched from (this matters for scheduled runs).
HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

RECIPIENT = "brad@prac3.com.au"
SENDER = "Catalyst Digest <onboarding@resend.dev>"  # Resend's free sender; swap to your own domain later
MODEL = "claude-sonnet-4-6"  # cheap + fast. claude-haiku-4-5-20251001 = cheaper, claude-opus-4-8 = deeper

LOOKBACK_DAYS = 8            # weekly run, with a day of overlap so nothing falls between briefs
HISTORY_FILE = os.path.join(HERE, "digest_history.json")
HISTORY_WINDOW_DAYS = 90     # how far back the do-not-repeat memory reaches
HISTORY_PROMPT_LIMIT = 150   # most recent N covered items shown to the model

CATEGORIES = [
    "Funding & raises",
    "New tools & launches",
    "Ideas & trends",
    "AU / APAC spotlight",
]

# The exact JSON shape the agent must return. Kept out of the f-string
# below so the braces don't need escaping.
SCHEMA = """{
  "items": [
    {
      "category": "one of the category names exactly as given",
      "headline": "short factual headline",
      "summary": "one or two factual sentences",
      "source": "publication or company name",
      "region": "e.g. US, EU, AU, APAC, Global",
      "url": "direct link to the source",
      "update_of_previous": false
    }
  ]
}"""


# --- 0. The do-not-repeat memory ---------------------------------------
def normalise(text):
    """Lowercase, strip punctuation: used for loose headline matching."""
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def load_history():
    """Return previously covered items, dropping anything past the window."""
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    cutoff = datetime.date.today() - datetime.timedelta(days=HISTORY_WINDOW_DAYS)
    kept = []
    for item in data.get("items", []):
        try:
            covered = datetime.date.fromisoformat(item.get("covered", ""))
        except ValueError:
            continue
        if covered >= cutoff:
            kept.append(item)
    return kept


def save_history(history, new_items):
    """Append this run's items and write the pruned history back to disk."""
    today = datetime.date.today().isoformat()
    for item in new_items:
        history.append({
            "headline": item.get("headline", ""),
            "url": item.get("url", ""),
            "category": item.get("category", ""),
            "covered": today,
        })
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": history}, f, indent=2, ensure_ascii=False)


def history_block(history):
    """The already-covered list, formatted for the prompt."""
    if not history:
        return "Nothing has been covered yet: this is the first brief."
    recent = history[-HISTORY_PROMPT_LIMIT:]
    return "\n".join(f"- ({i.get('covered')}) {i.get('headline')}" for i in recent)


# --- 1. Run the research agent -----------------------------------------
def build_prompt(history):
    today = datetime.date.today().strftime("%d %B %Y")
    cats = ", ".join(CATEGORIES)
    return f"""Today is {today}. You are a research agent compiling a weekly intelligence brief on digital health, health-tech and med-tech startups, for a clinician who follows this space closely.

Search the web for genuinely recent developments from roughly the last {LOOKBACK_DAYS} days. Cover ALL of these categories: {cats}.

Prioritise concrete signal: named startups, funding rounds (amount and lead investor), product / tool / device launches, regulatory clearances (FDA, TGA), and notable new ideas. Skip generic opinion pieces and anything clearly older than {LOOKBACK_DAYS} days. Surface Australian / APAC activity wherever it exists.

ALREADY COVERED IN PREVIOUS BRIEFS. Do not send these again:
{history_block(history)}

The novelty rule matters more than filling every category. Do not include any story from that list again, and do not reintroduce the same underlying story under a reworded headline or via a different publication's write-up of it. The single exception: if a listed story has a genuinely material new development since it was covered (a follow-on round, a regulatory decision, a launch, a trial readout, a failure), you may include it, set "update_of_previous" to true, and write the summary so it leads with what is new rather than restating the original news. A category with two genuinely new items is better than one padded to six with recycled ones.

Return ONLY a JSON object of exactly this shape:
{SCHEMA}

Aim for 3 to 6 items per category where genuinely new news supports it. Use the category names exactly as written above. If a category has nothing new, simply return no items for it. No preamble, no markdown code fences, no commentary outside the JSON."""


def extract_json(text):
    """Pull the JSON object out of the model's reply and parse it."""
    text = text.replace("```json", "").replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in the model's response")
    return json.loads(text[start:end + 1])


def run_agent(history):
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
        messages=[{"role": "user", "content": build_prompt(history)}],
    )
    # The response holds several block types (the searches, their results, and
    # the text Claude wrote). We keep only the text, then parse out the JSON.
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        data = extract_json(text)
    except (ValueError, json.JSONDecodeError) as err:
        print(f"Could not parse the agent's response as JSON: {err}")
        print("--- raw response ---")
        print(text[:4000])
        sys.exit(1)
    return data.get("items", [])


def drop_repeats(items, history):
    """Safety net under the prompt: bin anything already on the record."""
    seen_urls = {(i.get("url") or "").strip().lower() for i in history if i.get("url")}
    seen_heads = {normalise(i.get("headline")) for i in history}

    fresh = []
    for item in items:
        if item.get("category") not in CATEGORIES:
            continue
        if item.get("update_of_previous"):
            fresh.append(item)
            continue
        url = (item.get("url") or "").strip().lower()
        head = normalise(item.get("headline"))
        if url and url in seen_urls:
            continue
        if head and head in seen_heads:
            continue
        fresh.append(item)
    return fresh


# --- 2. Render the items, then wrap them in a tidy email shell ---------
def render_fragment(items):
    parts = []
    for category in CATEGORIES:
        parts.append(f"<h2>{html.escape(category)}</h2>")
        rows = [i for i in items if i.get("category") == category]
        if not rows:
            parts.append("<p>Nothing new since the last brief.</p>")
            continue
        for item in rows:
            flag = ' <span class="update">update</span>' if item.get("update_of_previous") else ""
            parts.append(f"<h3>{html.escape(item.get('headline', ''))}{flag}</h3>")
            parts.append(f"<p>{html.escape(item.get('summary', ''))}</p>")
            parts.append(
                '<p class="meta">'
                f"{html.escape(item.get('source', ''))} &middot; "
                f"{html.escape(item.get('region', ''))} &middot; "
                f'<a href="{html.escape(item.get("url", ""), quote=True)}">read source</a>'
                "</p>"
            )
    return "\n".join(parts)


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
    span.update {{ font-family:monospace; font-size:10px; letter-spacing:1px; text-transform:uppercase; color:#e8602f; vertical-align:middle; }}
    a {{ color:#e8602f; }}
  </style>
  {fragment}
  <p style="font-family:monospace; font-size:11px; color:#aaa; margin-top:32px; border-top:1px solid #eee; padding-top:12px;">
    Compiled automatically by your Catalyst agent.
  </p>
</div>"""


# --- 3. Send it --------------------------------------------------------
def send_email(html_body):
    resend.api_key = os.environ["RESEND_API_KEY"]
    today = datetime.date.today().strftime("%d %b %Y")
    resend.Emails.send({
        "from": SENDER,
        "to": [RECIPIENT],
        "subject": f"Catalyst: health-tech brief, {today}",
        "html": html_body,
    })


def main():
    history = load_history()
    print(f"Do-not-repeat list holds {len(history)} items from the last {HISTORY_WINDOW_DAYS} days.")

    print("Researching... (give it a minute while the agent searches the web)")
    items = run_agent(history)
    print(f"Agent returned {len(items)} items.")

    items = drop_repeats(items, history)
    print(f"{len(items)} left after filtering against the history.")

    if not items:
        print("Nothing new this week; not sending an empty email.")
        return

    send_email(wrap_email(render_fragment(items)))
    save_history(history, items)
    print(f"Done. Brief sent to {RECIPIENT} and history updated.")


if __name__ == "__main__":
    main()
