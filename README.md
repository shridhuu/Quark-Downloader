# Quark Downloader

A small, local-first command-line tool for downloading **Quark Cloud Drive** shares you are authorized to access — with fast parallel range downloads, multi-file concurrency, bandwidth throttling, automatic retries, zero-assembly resume, and live speed / ETA.

Paste a `pan.quark.cn/s/...` link, scan Quark's official QR login once, and download the whole share or just the files you want.

> This project is not affiliated with Quark / UC / Alibaba. Use it only for files you own or are authorized to access, and respect Quark's terms and applicable law.

---

## ✨ Key Features

* **⚡ Fast Parallel Downloads:**
  * **Range Chunks:** Every large file is split into 1 MiB range chunks fetched by up to 32 connections at once.
  * **Connection Reuse:** Keep-alive sessions are pooled across chunks and files — no TCP/TLS handshake overhead per chunk.
  * **Zero-Assembly In-Place Writing:** Chunks write directly into the preallocated file at their exact byte offsets. No thousands of separate chunk files, no assembly pass, and **no 2× disk space** requirement!
* **🚀 Multi-File Concurrency:** Download multiple files simultaneously with `--parallel-files N` while sharing a global connection budget.
* **🛑 Bandwidth Throttling:** Cap download speeds with `--limit-rate` (e.g. `--limit-rate 5M` or `--limit-rate 500K`).
* **⚡ Instant Startup:** Verified login sessions are cached locally. After the first QR scan, subsequent runs execute instantly without launching Chromium.
* **📊 Live Progress:** Per-file and overall progress with rolling transfer **speed** and **ETA**, plus an average-speed summary at the end.
* **🔁 Resume Anywhere:** Interrupted downloads resume from a compact `.meta` manifest and in-place `.part` file. Completed files and chunks are never re-downloaded.
* **🛡️ Self-Healing:**
  * **Smart Retries:** Exponential backoff with jitter and `Retry-After` header support.
  * **Expired Links:** If Quark's download link expires mid-transfer (HTTP 401/403), a fresh one is minted automatically.
  * **Cloud Folder Preservation:** If a download is cancelled or interrupted, the temporary cloud folder is preserved and reused on your next run with `--temp-folder`.
  * **No Batch Aborts:** One failing file never stops the rest — failures are listed at the end.
* **🎯 Download Only What You Need:** Pick individual files interactively (`--pick`) or filter with glob patterns (`--include` / `--exclude`).
* **🔐 Safe Login:** Official QR flow in a real browser window. No passwords, SMS codes, cookies, tokens, or URLs are ever requested or printed.
* **🧹 Clean Cloud:** The temporary folder created in your own Quark drive is deleted automatically upon full success.
* **🪟 Windows-Safe File Names:** Characters like `< > : " | ? *`, trailing dots/spaces, and reserved names (`CON`, `NUL`, …) are sanitized.
* **✅ Verified Output:** Files are only marked complete when their byte size matches Quark's metadata.

---

## 📋 System Requirements

* **Python 3.10+**
* **Chromium** (installed through Playwright — one command, shown below)
* A **Quark account** and the **Quark mobile app** (to scan the login QR)
* A graphical session for the first login (a browser window opens once for QR scan)

Python dependencies (`playwright`, `requests`) are installed automatically.

---

## 🚀 Installation

### 1. Install Python

Download Python 3.10 or newer from [python.org](https://www.python.org/downloads/) (tick **Add python.exe to PATH**), or use a package manager:

```powershell
# Windows
winget install Python.Python.3.12
```

```bash
# macOS
brew install python

# Ubuntu / Debian
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
```

*Verify installation:*

```bash
python --version
```

### 2. Get Quark Downloader

```bash
git clone https://github.com/shridhuu/Quark-Downloader.git
cd Quark-Downloader
```

### 3. Install & Run

#### Option A: Direct Run (No Virtual Environment Needed)

You can run directly using your global Python installation:

```bash
# 1. Install dependencies & chromium once (if not already installed)
pip install -e .
python -m playwright install chromium

# 2. Run anywhere!
quarkdl list "https://pan.quark.cn/s/your-share-id"
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads

# Or run directly with python inside the folder (no pip install needed):
python main.py download "https://pan.quark.cn/s/your-share-id" -o downloads
```

#### Option B: Isolated Virtual Environment (Optional)

If you prefer keeping dependencies inside an isolated folder:

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
```

> If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope Process Bypass` and try again.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

*Verify installation:*

```bash
quarkdl --help
```

---

## 🔑 First Run & Login

1. Run any command, e.g. `quarkdl list "https://pan.quark.cn/s/your-share-id"`.
2. A **Chromium window opens** on Quark's official login page.
3. **Scan the QR code** with the official Quark mobile app.
4. Your verified login is saved in `~/.quarkdl/browser-profile/cookies.json`. Later runs start **instantly** without opening Chromium!

If you need more time to scan on the first run:

```bash
quarkdl --login-timeout 600 list "https://pan.quark.cn/s/your-share-id"
```

---

## 🧭 Usage

Always wrap the link in **double quotes** so your shell does not mangle `?` or `#`.

### Quick start (Interactive List & Download)

The simplest way is running `main.py` directly:

```bash
# Run interactive wizard (prompts for URL -> displays list -> choose items to download)
python main.py

# Or pass the URL directly to list files and download on the spot:
python main.py "https://pan.quark.cn/s/your-share-id"
```

You can enter numbers or ranges like `1-3, 5` or `all` to download immediately from the displayed list.

### Download via command line flags

```bash
# 1. Download all files without prompts
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --yes

# 2. Pick specific files interactively (supports 1-5, 8, all)
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --pick
```

### Share with a passcode

Use the full link including the passcode parameter:

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id#/list/share?pwd=1234" -o downloads
```

### Download everything, no prompts

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --yes
```

### Download multiple files at once

Download 4 files concurrently:

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --parallel-files 4
```

### Limit download speed

Throttle bandwidth to avoid saturating your connection:

```bash
# Cap to 5 MiB/s
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --limit-rate 5M

# Cap to 500 KiB/s
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --limit-rate 500K
```

### Download a specific file from a folder

**Option A — pick by number:**

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --pick
```

Lists every file in the share with a number. Enter comma-separated numbers (e.g. `3,7,12`) or press Enter for all.

> Note: Do not combine `--pick` with `--yes` (`--yes` takes precedence).

**Option B — filter by glob pattern:**

```bash
# one exact file
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --yes --include "**/episode05.mp4"

# one file type inside a specific folder
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --yes --include "**/Season 1/*.mkv"
```

> **Always start `--include` / `--exclude` patterns with `**/`.** Patterns match paths relative to the temporary cloud folder.

### Filter with include / exclude

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" \
  -o downloads \
  --include "**/*.zip" \
  --exclude "**/preview/*"
```

### Tune speed and reliability

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" \
  -o downloads \
  --workers 32 \
  --parallel-files 3 \
  --chunk-size 1048576 \
  --retries 5
```

### Resume an interrupted download

Just run the **same command again**. Finished files and completed chunks are recognized instantly and skipped. If a download was interrupted, the cloud folder is automatically preserved and reused.

---

## 📖 Command Reference

```text
quarkdl [global options] <command> [command options]
```

### Global options (place before the command)

| Option | Default | Description |
|---|---|---|
| `--profile-dir PATH` | `~/.quarkdl/browser-profile` | Browser profile storing login state & cookie cache |
| `--headless` | off | Run browser headless (not useful for first QR login) |
| `--login-timeout SECONDS` | `180` | Timeout in seconds to wait for QR login scan |

### `quarkdl list <url>`

Prints every folder and file in the share with sizes and totals. Nothing is saved or downloaded.

### `quarkdl download <url> [options]`

| Option | Default | Description |
|---|---|---|
| `-o`, `--output PATH` | `downloads` | Folder to save files into |
| `--yes` | off | No prompts; download all selected files |
| `--pick` | off | Interactively choose individual files |
| `--include GLOB` | all files | Only download matching paths. Repeatable. Start with `**/` |
| `--exclude GLOB` | none | Skip matching paths. Repeatable. Start with `**/` |
| `--parallel-files N` | `1` | Number of files to download concurrently |
| `--workers N` | `32` | Total concurrent range workers |
| `--chunk-size BYTES` | `1048576` (1 MiB) | Range chunk size in bytes |
| `--limit-rate SPEED` | unlimited | Bandwidth cap (e.g. `5M`, `500K`, `10MB/s`) |
| `--retries N` | `5` | Attempts per chunk/file before giving up |
| `--keep-cloud` | off | Keep the temporary cloud folder in Quark drive |
| `--temp-folder NAME` | `quarkdl-<id>-<time>` | Temporary cloud folder name |

**Exit codes:** `0` success · `1` error or at least one file failed.

---

## 📂 What You Get

```text
📂 downloads/
 ├── My Share Folder/
 │   ├── video01.mkv
 │   └── video02.mkv
 ├── loose-file.zip
 └── .quarkdl-status.json      ← progress state (filenames + sizes; keep private)
```

While downloading, you may see:

| Item | Meaning |
|---|---|
| `file.ext.part` | Preallocated target file receiving chunks in-place |
| `file.ext.meta` | Lightweight JSON manifest recording completed chunk indices |
| `file.ext.size_mismatch_<N>` | Existing file with unexpected size moved aside safely |

Progress updates every ~1s with rolling speed and overall ETA:

```text
[2/5] My Share Folder/video02.mkv (734003200 bytes)
   42.3% 310.5 MiB/700.0 MiB  18.2 MiB/s | all  61.0% ETA 4m12s | quarkdl-abc-2026.../My Share Folder/video02.mkv
Transferred 1.9 GiB in 3m41s (avg 8.8 MiB/s)
```

---

## ⚙️ How It Works

1. **Auth:** Uses cached browser cookies for instant startup; refreshes via Playwright QR flow only when expired.
2. **Cloud Staging:** Saves selected share entries into a temporary folder in your Quark drive.
3. **Link Minting:** Fetches authorized download URLs through desktop-client endpoints with automatic refresh on expiration.
4. **Direct-to-File In-Place Streaming:**
   - Preallocates the `.part` file to expected size.
   - Range workers fetch 1 MiB chunks and write directly to their byte offsets under a fast mutex lock.
   - Tracks completed chunks in `.meta`.
   - On completion, instantly renames `.part` → final file with **zero assembly** and **zero extra disk space**.
5. **Clean Cloud:** Deletes the temporary cloud folder automatically upon 100% completion (or preserves it for seamless resume if interrupted).

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| `quarkdl` is not recognized | Activate virtual environment, or run `python -m quarkdl.cli ...` |
| PowerShell blocks `Activate.ps1` | Run `Set-ExecutionPolicy -Scope Process Bypass` |
| Chromium missing | Run `python -m playwright install chromium` |
| `Timed out waiting for Quark QR login` | Use `--login-timeout 600` |
| Speed is slow or jumps up and down | See the **Speed tips** section below |
| `HTTP 403` / link expired | Handled automatically with fresh link minting |
| Cloud cleanup failed | Delete `quarkdl-<id>-<time>` folder manually in Quark web/app |
| `--include` matches nothing | Start patterns with `**/` and check paths with `quarkdl list` |
| Download was interrupted | Re-run the exact same command — it reuses the cloud folder and resumes chunks |

---

## 🏎️ Speed tips

The progress line shows the live speed and how many connections are open:

```text
   42.3% 310.5 MiB/700.0 MiB  3.2 MiB/s | all  61.0% ETA 4m12s | conn 32/32 | path/to/file.mkv
```

Use the `conn` number to see where the limit is:

| What you see | What it usually means | What to try |
|---|---|---|
| `conn 32/32` all the time, but the speed is low | Quark/CDN is limiting each connection (or your account). More work from this tool will not help | Raise `--workers` (e.g. `64`) and see if the total goes up |
| `conn` well below the maximum | Connections are being dropped, retried or waiting | Check for `slow restarts` and failures; lower `--workers` if the CDN is rejecting connections |
| `(slow restarts N)` keeps growing | Individual connections are crawling and are being replaced | Normal in small numbers; if it never stops, try fewer `--workers` |

Notes:

* Speed is measured on bytes as they arrive, over a rolling 10-second window, so brief dips and bursts are smoothed out. Real swings still show up.
* A connection that delivers almost nothing is dropped after about 5 seconds and re-opened, keeping the bytes it already received. A connection that stalls completely times out after 20 seconds.
* Another program downloading at the same time (a browser, for example) shares your bandwidth, and Quark may serve a different file at a different speed than the one you are comparing against.
* `--limit-rate` turns the slow-connection check off, because the limiter itself slows connections down.

---

## 🔒 Safety Notes

* Login happens strictly through Quark's official web page and app QR flow.
* Stored locally in your Playwright profile directory.
* Never prints cookies, tokens, or private URLs.
* Temporary cloud folders are auto-deleted upon successful completion.

---

## 🗺️ Ideas / Roadmap

- [x] Connection pooling & keep-alive reuse
- [x] Direct-to-file in-place chunk writing (zero assembly, no 2× disk space)
- [x] Multi-file parallel downloads (`--parallel-files`)
- [x] Bandwidth throttle (`--limit-rate`)
- [x] Fast cookie caching & browser skip
- [x] Cloud folder preservation on interruption & automatic reuse
- [ ] Dynamic chunk splitting / work stealing at download tail
- [ ] Direct download from user's personal drive (without share link)

---

## 👨‍💻 Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

```text
📂 Quark-Downloader/
 ├── src/quarkdl/
 │   ├── cli.py          ← CLI commands & argument parsing
 │   ├── api.py          ← Quark Cloud Drive API client
 │   ├── browser.py      ← Playwright QR login & cookie caching
 │   ├── downloader.py   ← in-place range downloader, pool, rate limiter
 │   ├── progress.py     ← thread-safe progress, speed & ETA reporter
 │   └── core.py         ← URL parsing, path sanitizing, helpers
 └── tests/              ← unit tests & local HTTP Range server tests
```

---

## 🙏 Credits

Built by [Shridhuu](https://github.com/shridhuu). Based on [Noyv3x/quark-drive-resumable-downloader](https://github.com/Noyv3x/quark-drive-resumable-downloader) (MIT).

## 📄 License

MIT — see [LICENSE](LICENSE).
