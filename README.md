# 🃏 CardFlow

**Scan a stack of trading cards and get priced eBay listings ready to publish.**

Feed cards through a document scanner (or drag photos in). CardFlow identifies each
card with AI, prices it from real sold comps, writes the title and item specifics,
uploads the photos, and creates an eBay draft. You review the drafts, fix anything
that needs it, and publish them all at once. Once cards are live, CardFlow also
reprices them, promotes them, and converts them to auctions.

It runs on your own computer. Your keys, listings and scans never leave your machine
except to go to the services you set up yourself (eBay, Google/OpenAI, Cloudinary).

---

## Contents

- [Features](#-features)
- [What you need](#-what-you-need)
- [Setup](#-setup) (about 30-45 minutes, once)
- [Daily use](#-daily-use)
- [Costs](#-costs)
- [Troubleshooting](#-troubleshooting)
- [Configuration reference](#-configuration-reference)
- [For developers](#-for-developers)

---

## ✨ Features

### Scanning and intake
- **Live scanner intake.** Point CardFlow at the folder your scanner saves to and press
  Start. Cards show up and identify themselves while the stack is still feeding.
  It was built on a Fujitsu fi-7160 with PaperStream Capture, but any scanner that saves
  JPEGs to a folder works.
- **Automatic front/back pairing.** Images pair up in the order they arrive (front,
  back, front, back), so filenames don't matter.
- **Single-sided and manual uploads.** No scanner? Upload photos in the sidebar. Cards
  without a back scan are fine.
- **Misfeed recovery.** If a sheet gets dropped, one click queues the leftover image as
  a single-sided card or clears the queue so pairing starts fresh.
- **Duplicate protection.** Every scan you've listed is remembered by its content, not
  its filename. Pressing Start again, or a scanner that restarts its numbering, never
  lists the same card twice.
- **Card cap with no lost scans.** Intake pauses at a set number of cards (150 by
  default) to keep the page fast. Extra scans wait in the queue until you make room.
- **Survives restarts.** Your card list is saved to disk. Close the app, reboot, come
  back: drafts and edits are still there. A card that was mid-identification when the
  app closed is flagged so you can retry it.
- **One-click rotation.** ⟲ / ⟳ buttons fix sideways scans, front and back separately.
  The corrected photo is what gets uploaded.

### AI card identification
- **Gemini or OpenAI vision.** Gemini Flash is the default at roughly 1.5¢ a card.
  OpenAI GPT models are supported too.
- **Built for the details collectors search for:** parallel, serial number, print run,
  insert, rookie card, autograph, promo stamp, grade. The AI gets zoomed close-ups of
  each corner so small foil serials and card codes are readable.
- **Second-look serial check.** When a serial reading looks doubtful, a focused zoomed
  pass re-reads just the stamp.
- **Parallel checklists.** A built-in catalogue of parallels and print runs (Topps Chrome,
  Topps, Bowman and more) checks the AI's answer: a /399 in Topps Chrome Basketball can
  only be Magenta, so CardFlow fills that in. A parallel that doesn't match its serial
  is flagged.
- **A parallel library that learns.** Every card you publish is kept as a labelled
  example of its parallel (X-Refractor, Ray Wave, Blue Wave...), and future scans are
  compared against those examples. The repo ships with a starter library.
- **Free-tier fallback.** If your paid Gemini credit runs out mid-stack, CardFlow can
  switch to a free-tier key so the batch keeps going. A banner tells you when it does.
- **Out-of-credit handling.** If the AI account is empty, you get one clear banner with an
  "Add credit" link and a one-click retry for every failed card, instead of a screen of red errors.

### Card types
| Type | What's special |
|---|---|
| **Sports** (NBA, NFL, MLB, NHL, soccer, F1, UFC, WWE...) | Season years ("2025-26"), Chrome vs paper and Update vs Flagship from card codes, rookie and auto detection, Sport/League/Team specifics, Formula 1 parallel ladder |
| **Pokémon** | Checked against pokemontcg.io; rarity, reverse holo, set and number |
| **One Piece** | Checked against optcgapi.com. Tells **SP**, **Manga** and **Alt Art** apart from the base print by comparing your scan with the official card images, so a $1,000 Manga is never listed as a $2 base card |
| **Union Arena** | Full card codes and star rarities (SR★★ is written "SR 2 Star", since eBay bans the ★ symbol) |
| **Non-sport / entertainment** (Disney, Pixar, Star Wars, Marvel, Garbage Pail Kids...) | Listed in eBay's Non-Sport category with Character and Franchise specifics instead of being misfiled as sports cards |
| **ESCO music cards** (ESCO x LISA, ESCO x KATSEYE) | Subsets (PRESS PLAY, Alter Ego), foil colour, Magma Foils, /800-to-1/1 serials, hand-signed autos |

### Titles and listings
- **SEO titles in a seller's style.** Year, brand, set, player, number, insert, parallel,
  print run, RC, Auto and grade, deduplicated ("Topps Topps Chrome" never happens) and
  kept under eBay's 80-character limit without cutting off the parallel.
- **eBay-safe text.** Symbols eBay rejects (★ ™ ® ♥ ½, fullwidth characters) are
  converted or removed automatically, even in titles you type yourself.
- **Complete item specifics,** filled in for the card type. Values are trimmed to eBay's
  65-character limit so a listing never fails on a long value.
- **Title warnings** for an unusual print run (a serial read as /18 is probably /10) or
  an impossible serial (number higher than the run).
- **Year sync.** Fix the year in the title and Year Manufactured/Season follow it.
- **Category guard.** eBay's category suggestions are checked and corrected at publish
  time ("Washer & Dryer Parts" for a Disney card does happen).
- **Clean, condition-aware descriptions** with a corners/edges/surface/centering breakdown.

### Pricing
- **Sold comps first.** Prices come from sources in order of trust:
  1. your own past sales (pulled from eBay),
  2. TCG market prices (TCGplayer for Pokémon, the One Piece card database),
  3. sold-comps.com (real completed eBay sales),
  4. PriceCharting / SportsCardsPro,
  5. current eBay asking prices, adjusted down by your own measured ask-to-sold ratio.
- **Best Offer aware.** eBay now shows accepted offer amounts. You choose whether they
  count as comps.
- **NEEDS PRICE REVIEW flag** on thin or wildly spread comps, or an unsure card ID. Bulk
  publish skips flagged cards until you've looked at them.
- **One-click comp links** to eBay sold listings, PriceCharting or SportsCardsPro for every card.

### Auctions and offers
- **Auto format choice.** Cards under a price you set (default $50) start as 7-day
  auctions; everything else is Buy It Now. Cards with an unsure identity are always
  Buy It Now.
- **Smart opening bids** from comps, never below your floor, showing what each bid nets
  after eBay fees.
- **Best Offer floors** set automatically (default 20% of price). Switching a card to
  Buy It Now turns offers on.

### Publishing
- **Review screen** for every card: edit title, price, description, format, offers and
  the opening bid, 25 cards per page.
- **Select all / Publish selected / Publish ALL confident.**
- **Shipping by price:** cards $20 and under use your envelope policy, cards over $20 your
  tracked policy.
- **Photos hosted on Cloudinary** automatically.
- **Results that stay on screen.** The publish summary, eBay's exact error messages and
  the listing links stay up until you dismiss them, even while scanning.
- **Scan cleanup:** published scans are moved to a `published` subfolder (or deleted) so
  the next stack starts clean.
- **Discord-ready rows** (`$ebay view <url>`) for every new listing.

### Repricer page
- Loads your live listings and prices each one fresh, using the same pricing as new scans.
- Shows only the listings worth changing (default ±15%), biggest moves first, with a
  cooldown so the same cards don't churn every week.
- Bulk-select only the sold-comp-backed suggestions, then update prices on eBay in one go.
  It works on listings created outside CardFlow too, through eBay's older Trading API.
- Moves Best Offer floors onto your current policy as listings are repriced.
- **Convert to auction:** ends a stale Buy It Now listing and relaunches it as an auction
  with a suggested opening bid.

### Promote page
- Ranks listings that have sat unsold and suggests a fix for each: promote it, or
  lower the price because the comps say it's overpriced.
- **Promote from CardFlow** with eBay Promoted Listings Standard (you pay only when it
  sells). Set the ad rate per listing, update it or stop promoting, or promote every
  suggested listing at once.
- Convert to auction from the same page.

---

## 🧰 What you need

| | Required? | Cost |
|---|---|---|
| A Windows, Mac or Linux computer with **Python 3.9-3.12** | Yes | Free |
| An **eBay seller account** with business policies | Yes | Free |
| An **eBay developer account** (to get API keys) | Yes | Free |
| A **Google Gemini API key** (or an OpenAI key) | Yes | ~1.5¢ a card |
| A **Cloudinary account** (hosts listing photos) | Yes | Free tier is plenty |
| A document scanner that saves to a folder | No (you can upload photos) | |
| sold-comps.com key (real sold prices) | Recommended | Free, 100 lookups/month |
| PriceCharting API token | No | Paid |

---

## 🚀 Setup

### 1. Install Python and get the code

1. Install **Python 3.11** from [python.org/downloads](https://www.python.org/downloads/).
   On Windows, **tick "Add python.exe to PATH"** on the first installer screen.
2. Get CardFlow, either way:
   - **With git:** `git clone https://github.com/seth-walter/cardflow.git`
   - **Without git:** on the GitHub page click **Code → Download ZIP**, then unzip it
     somewhere outside OneDrive/Dropbox (e.g. `C:\CardFlow`).

### 2. First launch

- **Windows:** double-click **`start.bat`**.
- **Mac/Linux:** open a terminal in the folder and run `chmod +x start.sh && ./start.sh`.

The first run installs everything, creates your **`.env`** settings file from
`.env.example`, and opens it (on Windows, in Notepad). Steps 3-6 fill it in. Keep `.env`
private: it holds your passwords and tokens.

<details>
<summary>Prefer doing it by hand?</summary>

```bash
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac/Linux
pip install -r requirements.txt
copy .env.example .env         # Windows  (Mac/Linux: cp .env.example .env)
```
</details>

### 3. AI key (Gemini)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and click
   **Create API key**.
2. Turn on billing for that key's Google Cloud project. Free-tier keys work, but they're
   slow under rate limits and Google may use free-tier requests for training.
3. In `.env`:
   ```
   VISION_PROVIDER=gemini
   GEMINI_API_KEY=your-key
   ```
   *(Optional)* A second key from a project with billing **off** can go in
   `GEMINI_FREE_API_KEY`. It's used only if the paid key runs out of credit.

To use OpenAI instead, set `VISION_PROVIDER=openai` and `OPENAI_API_KEY` (from
[platform.openai.com/api-keys](https://platform.openai.com/api-keys)).

### 4. eBay developer keys

1. Sign up at [developer.ebay.com](https://developer.ebay.com/) with your normal eBay
   account, then open **Hi <name> → Application Keys**.
2. Create a **Production** keyset (not Sandbox). eBay first asks about *Marketplace
   Account Deletion notifications*. CardFlow stores no other users' data, so choose
   **"I do not persist eBay data"** to be exempted.
3. Copy into `.env`:
   ```
   EBAY_CLIENT_ID=      (the App ID)
   EBAY_CLIENT_SECRET=  (the Cert ID)
   ```
4. Next to the Production keyset, click **User Tokens → Get a Token from eBay via Your
   Application → Add eBay Redirect URL**. Fill in a display title, leave the URLs at
   their defaults, and save. Copy the **RuName** it shows (it looks like
   `Your_Name-YourApp-PRD-abc123-defg`) into `.env`:
   ```
   EBAY_RUNAME=
   ```

### 5. Connect your eBay account

```bash
venv\Scripts\python tools\ebay_consent.py        # Windows
venv/bin/python tools/ebay_consent.py            # Mac/Linux
```

A browser opens on eBay's sign-in page. Approve access. eBay then sends you to a page
whose address contains `code=...`. **Copy the whole address bar** and paste it into the
terminal. Your tokens are saved to `.env`, and CardFlow renews them automatically from
then on.

### 6. Shipping, payment and return policies

CardFlow uses your eBay **business policies**: one payment, one return, and **two
shipping** policies. Create any you're missing in **Seller Hub → Account → Business
policies** ([direct link](https://www.bizpolicy.ebay.com/businesspolicy/manage)):

| Policy | Suggested setup |
|---|---|
| Shipping, **$20 and under** | eBay Standard Envelope (or USPS letter), package type *Letter/Envelope*. Include a small handling fee if you like |
| Shipping, **over $20** | USPS Ground Advantage, package type *Package/Thick envelope* (tracked) |
| Payment | Immediate payment required |
| Return | Your choice (e.g. 30 days, buyer pays) |

Then run the setup helper. It lists your policies, asks which to use, saves the IDs, and
creates your shipping location from your ZIP code:

```bash
venv\Scripts\python tools\ebay_setup.py          # Windows
venv/bin/python tools/ebay_setup.py              # Mac/Linux
```

### 7. Photo hosting (Cloudinary)

1. Sign up free at [cloudinary.com](https://cloudinary.com/).
2. Your **Cloud name** is on the dashboard → `CLOUDINARY_CLOUD_NAME`.
3. **Settings (⚙) → Upload → Upload presets → Add upload preset.** Set **Signing mode:
   Unsigned**, save, and copy the preset name → `CLOUDINARY_UPLOAD_PRESET`.

### 8. Sold comps (recommended)

Sign up at [sold-comps.com](https://sold-comps.com) (free, 100 lookups a month) and put
the key in `SOLDCOMPS_API_KEY`. Without it, prices come from asking prices and are
flagged as estimates. Results are cached for a week, so rescanning the same card costs
nothing.

Once you've sold a few cards, two optional tools make pricing sharper:

```bash
python tools/backfill_sales.py      # your own eBay sales become comps
python tools/calibrate_prices.py    # measures how far asks run above real sales
```

### 9. Scanner (optional)

**PaperStream Capture (Fujitsu fi-series)** settings; other scanners need the same ideas:

1. **Duplex** scanning, so each card gives a front then a back image.
2. Output **JPEG**, one file per page.
3. Save to a local folder **outside** OneDrive/Dropbox (syncing locks files mid-scan),
   and put that folder in `SCAN_INBOX_DIR`.
4. Turn **off** blank-page removal. A skipped page shifts every pair after it.
5. File names can be anything sequential.

### 10. Start it

Double-click **`start.bat`** (or run `./start.sh`). CardFlow opens at
**http://localhost:8501**. If anything required is still missing, the page lists exactly
which `.env` values to fill in.

---

## 📦 Daily use

1. **Start:** confirm the scan folder in the sidebar and press **▶ Start**.
2. **Feed the stack.** Cards appear as *IDENTIFYING*, then *DRAFT READY* with title,
   price, format and description filled in.
3. **Review.** Fix titles, prices or rotation, and check anything marked
   **NEEDS PRICE REVIEW** or with a title warning. Use the comp links to double-check.
4. **Publish:** **Select all for publish → Publish selected**, or **Publish ALL
   confident** to send everything that isn't flagged.
5. Read the summary that stays on screen (errors include eBay's exact message), then
   **Dismiss**.
6. **Clear all cards** in the sidebar when you're ready for a fresh batch.

Every so often, open **Repricer** to refresh prices on live listings, and **Promote**
to deal with cards that aren't selling.

> **Promoting** needs one extra eBay permission. If the Promote page asks for it, run
> `tools/ebay_consent.py` again.

---

## 💵 Costs

- **AI identification:** about **1.4-1.5¢ per card** on Gemini Flash. A 150-card stack is
  about $2.
- **eBay:** normal selling fees. Promoted Listings charge only the ad rate you pick, and
  only when a promoted card sells.
- **Cloudinary, sold-comps.com free tier, pokemontcg.io, optcgapi.com:** free.

---

## 🛠 Troubleshooting

| Problem | Fix |
|---|---|
| Page says "CardFlow is not set up yet" | Fill in the `.env` values it lists, then refresh |
| `No module named 'requests'` (or similar) | You're on a different Python. Use `start.bat`/`start.sh`, or activate the venv first |
| `invalid_scope` or 401 errors from eBay | Run `tools/ebay_consent.py` again |
| "Missing fulfillment policy ID" | Run `tools/ebay_setup.py` |
| Cards don't appear while scanning | Watcher not started, wrong folder in the sidebar, or the scanner is saving somewhere else |
| Fronts and backs swapped | A sheet was dropped: **Drop queued images** and rescan the cards after it |
| Intake stopped mid-stack | Card cap reached: publish and clear, or raise the cap in the sidebar |
| "Gemini/OpenAI is out of credit" | Add credit with the banner's link, then **Retry** |
| A listing failed with an eBay error | The error stays on screen with eBay's reason. Fix the card and publish it again |
| No photos on listings | Check the Cloudinary name and that the preset is **Unsigned** |
| A card was identified wrong | Edit the title. Year, specifics and description follow it on publish |

---

## ⚙️ Configuration reference

Everything lives in `.env`. See [.env.example](.env.example) for the full list with comments.
The ones people usually change:

| Setting | Default | What it does |
|---|---|---|
| `VISION_PROVIDER` | `gemini` | `gemini` or `openai` |
| `SCAN_INBOX_DIR` | `C:\Scans\CardScans` | Folder the scanner saves to |
| `MAX_CARDS_PER_SESSION` | `150` | Card cap before intake pauses |
| `SCAN_WORKERS` | `3` | Cards identified at the same time |
| `AUCTION_UNDER` | `50` | Cards priced below this start as auctions (`0` = never) |
| `AUCTION_DURATION` | `DAYS_7` | `DAYS_1`, `3`, `5`, `7` or `10` |
| `AUCTION_MIN_START` | `0.99` | Lowest opening bid you'll accept |
| `MIN_OFFER_RATIO` | `0.20` | Best Offers below this share of price are auto-declined |
| `SOLDCOMPS_OFFER_MODE` | `include` | Count accepted Best Offers as comps: `include`, `exclude`, `prefer` |
| `MIN_COMPS_FOR_CONFIDENCE` | `3` | Fewer comps than this flags NEEDS PRICE REVIEW |
| `ASK_TO_SOLD_FACTOR` | `1.0` | Multiplier for asking-price estimates (set by `calibrate_prices.py`) |

---

## 🧑‍💻 For developers

```
CardFlow.py              main page: live intake, review, publishing
pages/1_Repricer.py      reprice live listings, convert to auction
pages/2_Promote.py       promoted listings and stale-listing suggestions
config/settings.py       every .env setting, with defaults
core/                    scan watcher, pairing, session persistence, batch pipeline
services/                AI identification, pricing sources, card lookups, eBay APIs
utils/                   text cleanup, persistent notices, .env writer
tools/                   setup tools, pricing tools and offline tests
data/                    your local data (git-ignored) + shipped parallel checklists
```

**Tests** run offline. They make no API calls and spend no credit:

```bash
python tools/test_scan_pipeline.py
python tools/test_app_ui.py
python tools/test_publish.py
python tools/test_titles_and_auctions.py
python tools/test_aspects.py
python tools/test_non_sport.py
python tools/test_onepiece.py
python tools/test_repricer.py
```

`tools/eval_titles.py` scores the identifier against titles you corrected by hand. It
makes real AI calls (one per card per model), so it costs money.

Pull requests welcome. Keep tests offline, and never commit `.env` or anything in `data/`
except `parallels.json` and `parallel_refs/`.

---

## 📄 License

[MIT](LICENSE). Use it, change it, share it.

CardFlow is not affiliated with eBay, Google, OpenAI, Topps, Panini, Bandai, The Pokémon
Company or ESCO. You're responsible for your own listings and API usage.
