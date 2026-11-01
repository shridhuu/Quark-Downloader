# Quark Downloader

A small, local-first command-line tool for downloading **Quark Cloud Drive** shares you are authorized to access — with fast parallel range downloads, automatic retries, resume support, and live speed / ETA.

Paste a `pan.quark.cn/s/...` link, scan Quark's official QR login once, and download the whole share or just the files you want.

> This project is not affiliated with Quark / UC / Alibaba. Use it only for files you own or are authorized to access, and respect Quark's terms and applicable law.

---

## ✨ Key Features

* **⚡ Fast Parallel Downloads:**
  * **Range Chunks:** Every large file is split into 1 MiB range chunks fetched by up to 32 connections at once.
  * **Connection Reuse:** Keep-alive sessions are reused across chunks and files, so there is no new TCP/TLS handshake per chunk.
* **📊 Live Progress:** Per-file and overall progress with a rolling transfer **speed** and **ETA**, plus an average-speed summary at the end.
* **🔁 Resume Anywhere:** Interrupted downloads continue from the chunks (or `.part` file) already on disk. Finished files are skipped.
* **🛡️ Self-Healing:**
  * **Smart Retries:** Exponential backoff with jitter, and `Retry-After` is honored. Configure with `--retries`.
  * **Expired Links:** If Quark's download link expires mid-transfer (HTTP 401/403), a fresh one is fetched automatically.
  * **No Batch Aborts:** One failing file no longer stops the rest — failures are listed at the end.
* **🎯 Download Only What You Need:** Pick individual files interactively (`--pick`) or filter with glob patterns (`--include` / `--exclude`).
* **🔐 Safe Login:** Official QR flow in a real browser window. No passwords, SMS codes, cookies, tokens, or URLs are ever requested or printed.
* **🧹 Clean Cloud:** The temporary folder created in your own Quark drive is deleted automatically after the download.
* **🪟 Windows-Safe File Names:** Characters like `< > : " | ? *`, trailing dots/spaces, and reserved names (`CON`, `NUL`, …) are sanitized.
* **✅ Verified Output:** Files are only considered complete when their byte size matches Quark's metadata.

---

## 📋 System Requirements

* **Python 3.10+**
* **Chromium** (installed through Playwright — one command, shown below)
* A **Quark account** and the **Quark mobile app** (to scan the login QR)
* A graphical session for the first login (a browser window opens)

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

### 3. Create a virtual environment and install

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

> Open a new terminal later? Activate the virtual environment again (`.\.venv\Scripts\Activate.ps1` on Windows, `source .venv/bin/activate` elsewhere) before running `quarkdl`. You can also run it as `python -m quarkdl.cli ...`.

---

## 🔑 First Run & Login

1. Run any command, e.g. `quarkdl list "https://pan.quark.cn/s/your-share-id"`.
2. A **Chromium window opens** on Quark's official page.
3. **Scan the QR code** with the official Quark mobile app.
4. Your login is saved in `~/.quarkdl/browser-profile` (on Windows: `C:\Users\<you>\.quarkdl\browser-profile`), so later runs normally need no new scan until the login expires.

If you take longer than 3 minutes to scan, raise the timeout (note: global options go **before** the command):

```bash
quarkdl --login-timeout 600 list "https://pan.quark.cn/s/your-share-id"
```

---

## 🧭 Usage

Always wrap the link in **double quotes** so the shell doesn't mangle `?` and `#`.

### Quick start

```bash
# 1. See what's inside the share
quarkdl list "https://pan.quark.cn/s/your-share-id"

# 2. Download (asks which top-level entries you want; Enter = all)
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads
```

### Share with a passcode

Use the full link including the passcode:

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id#/list/share?pwd=1234" -o downloads
```

### Download everything, no prompts

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --yes
```

### Download a specific file from a folder

**Option A — pick by number:**

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --pick
```

Every file in the share is listed with a number. Enter comma-separated numbers (e.g. `3,7,12`) or press Enter for all.

> Don't combine `--pick` with `--yes` — `--yes` wins and downloads everything.

**Option B — pick by pattern:**

```bash
# one exact file
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --yes --include "**/episode05.mp4"

# one file type inside one folder
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --yes --include "**/Season 1/*.mkv"
```

> **Start every `--include` / `--exclude` pattern with `**/`.** Patterns are matched against the path inside the temporary cloud folder, so `Season 1/*.mkv` will not match. Run `quarkdl list` to see the exact paths.

### Filter with include / exclude

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" \
  -o downloads \
  --include "**/*.zip" \
  --exclude "**/preview/*"
```

Both flags can be repeated.

### Tune speed and reliability

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" \
  -o downloads \
  --workers 32 \
  --chunk-size 1048576 \
  --retries 5
```

### Keep the temporary cloud folder

```bash
quarkdl download "https://pan.quark.cn/s/your-share-id" -o downloads --keep-cloud
```

### Resume an interrupted download

Just run the **same command again**. Finished files are skipped and completed chunks are reused.

---

## 📖 Command Reference

```text
quarkdl [global options] <command> [command options]
```

### Global options (place before the command)

| Option | Default | Description |
|---|---|---|
| `--profile-dir PATH` | `~/.quarkdl/browser-profile` | Browser profile that stores your Quark login |
| `--headless` | off | Run the browser without a window. Not useful for the first QR login |
| `--login-timeout SECONDS` | `180` | How long to wait for you to scan the QR code |

### `quarkdl list <url>`

Prints every folder and file in the share with sizes and totals. Nothing is downloaded or saved to your drive.

### `quarkdl download <url> [options]`

| Option | Default | Description |
|---|---|---|
| `-o`, `--output PATH` | `downloads` | Folder to save files into |
| `--yes` | off | No prompts; download all selected files |
| `--pick` | off | Interactively choose individual files |
| `--include GLOB` | all files | Only download matching paths. Repeatable. Start with `**/` |
| `--exclude GLOB` | none | Skip matching paths. Repeatable. Start with `**/` |
| `--workers N` | `32` | Parallel range connections per file (must be ≥ 1) |
| `--chunk-size BYTES` | `1048576` (1 MiB) | Size of each range chunk (must be ≥ 1) |
| `--retries N` | `5` | Attempts per chunk / file before giving up (must be ≥ 1) |
| `--keep-cloud` | off | Don't delete the temporary folder from your Quark drive |
| `--temp-folder NAME` | `quarkdl-<share-id>-<timestamp>` | Name of the temporary cloud folder |

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

While a download is running you may also see:

| Item | Meaning |
|---|---|
| `file.ext.part` | A partially downloaded small file (resumes automatically) |
| `file.ext.chunks/` | Completed chunks of a large file, removed after assembly |
| `file.ext.size_mismatch_<N>` | An existing file with the wrong size that was moved aside |

Progress looks like this (updates about once per second):

```text
[2/5] My Share Folder/video02.mkv (734003200 bytes)
   42.3% 310.5 MiB/700.0 MiB  18.2 MiB/s | all  61.0% ETA 4m12s | quarkdl-abc-2026.../My Share Folder/video02.mkv
Transferred 1.9 GiB in 3m41s (avg 8.8 MiB/s)
```

---

## ⚙️ How It Works

1. Reads the share through Quark's web API using your own login.
2. Saves the chosen top-level entries into a **temporary folder in your own Quark drive** (web share downloads often reject large files, and generated CDN links are cookie-bound).
3. Requests a download link per file through the desktop-client-style endpoint.
4. Downloads large files as parallel range chunks, small files as a single resumable stream.
5. Assembles chunks, verifies byte sizes, then **deletes the temporary cloud folder**.

> Quark can only save whole top-level entries. To get one file out of a folder, the whole top-level folder is copied to your temporary cloud folder first, but only your selected files are downloaded. Make sure your Quark drive has enough free space for it.

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| `quarkdl` is not recognized | Activate the virtual environment, or run `python -m quarkdl.cli ...` |
| PowerShell blocks `Activate.ps1` | `Set-ExecutionPolicy -Scope Process Bypass` |
| *Executable doesn't exist* / browser missing | `python -m playwright install chromium` |
| `Timed out waiting for Quark QR login` | Scan faster or use `--login-timeout 600` |
| Login asked again every run | Make sure `--profile-dir` points to the same folder each time |
| Download is slow | Speed is limited by Quark / CDN / account throttling. Try `--workers 16` (too many connections can get throttled) |
| `HTTP 403` / link expired | Handled automatically with a fresh link; if it persists, re-run the command |
| *cloud cleanup failed* | Delete the `quarkdl-<share-id>-<timestamp>` folder manually in the Quark app |
| `--include` matches nothing | Start the pattern with `**/` and check paths with `quarkdl list` |
| Some files failed | They are listed at the end. Re-run the same command to resume |

---

## 🔒 Safety Notes

* Login happens through Quark's official web page and app QR flow.
* Login state is stored locally in your Playwright profile directory.
* The tool does not print raw cookies, `stoken`, generated download URLs, or account credentials.
* `.quarkdl-status.json` can contain filenames and sizes — keep it private if filenames are sensitive.
* Temporary cloud folders are named `quarkdl-<share-id>-<timestamp>` and deleted by default.

---

## ⚠️ Limitations

* Alpha tool built around observed Quark web/desktop endpoints; Quark may change them.
* The first QR login needs a graphical browser session (headless servers need a desktop or X forwarding).
* Download speed is subject to Quark / CDN / account throttling.
* Large files are assembled from chunks at the end, so you need roughly **2× the file size** in free disk space while it finishes.
* Files are downloaded one after another (each file uses many parallel connections).
* If a download fails, the temporary cloud folder is still deleted, so a re-run saves the share again.

---

## 🗺️ Ideas / Roadmap

Not implemented yet:

* Write chunks directly into a preallocated file (no assembly step, no 2× disk use)
* Download several files at once
* `--limit-rate` bandwidth cap
* Dynamic chunk splitting so no connection sits idle at the end of a file
* Cached login so the browser only opens when the session has expired
* Keep and reuse the cloud folder after a failure

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
 │   ├── cli.py          ← commands & flags
 │   ├── api.py          ← Quark API client
 │   ├── browser.py      ← Playwright QR login
 │   ├── downloader.py   ← range downloads, retries, resume
 │   ├── progress.py     ← speed / ETA reporter
 │   └── core.py         ← URL parsing, path sanitizing, helpers
 └── tests/              ← unit tests + local HTTP-server tests
```

---

## 🙏 Credits

Built by [Shridhuu](https://github.com/shridhuu). Based on [Noyv3x/quark-drive-resumable-downloader](https://github.com/Noyv3x/quark-drive-resumable-downloader) (MIT).

## 📄 License

MIT — see [LICENSE](LICENSE).
