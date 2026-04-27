# Quark Drive Resumable Downloader

A small, local-first CLI for downloading Quark Cloud Drive shares you are authorized to access.

It is designed for the simple path:

1. paste a `pan.quark.cn/s/...` link;
2. scan Quark's official QR login in a browser window;
3. list the share contents;
4. download everything or choose a subset;
5. resume interrupted downloads and verify byte counts.

> This project is not affiliated with Quark/UC/Alibaba. Use it only for files you own or are authorized to access, and respect Quark's terms and applicable law.

## Why this exists

Quark share downloads can be awkward for large files: web share download endpoints may reject large files, and generated CDN URLs are cookie-bound. This tool keeps the workflow local: it uses your own browser login, saves the share into a temporary folder in your own Quark drive, downloads through the desktop-client-style endpoint with range requests, verifies file sizes, and deletes the temporary cloud folder by default.

The tool deliberately avoids third-party parsing sites and never asks you to paste cookies, passwords, SMS codes, tokens, or download URLs.

## Quick start

```bash
git clone https://github.com/Noyv3x/quark-drive-resumable-downloader.git
cd quark-drive-resumable-downloader

python -m venv .venv
. .venv/bin/activate
pip install -e .
python -m playwright install chromium

quarkdl download 'https://pan.quark.cn/s/your-share-id' -o downloads
```

On the first run, a Chromium window opens. Scan the Quark login QR with the official Quark app. The browser profile is reused at `~/.quarkdl/browser-profile`, so later runs should not need another scan unless the login expires.

## Usage

### Download all files interactively

```bash
quarkdl download 'https://pan.quark.cn/s/your-share-id' -o downloads
```

The CLI lists the share root and prompts you to choose entries. Press Enter for all entries.

### Download everything without prompts

```bash
quarkdl download 'https://pan.quark.cn/s/your-share-id' -o downloads --yes
```

### Pick individual files interactively

```bash
quarkdl download 'https://pan.quark.cn/s/your-share-id' -o downloads --pick
```

This lists all files by number. The tool saves only the root entries needed to reach those files, then downloads just the chosen paths.

### Download only matching files

```bash
quarkdl download 'https://pan.quark.cn/s/your-share-id' \
  -o downloads \
  --include '**/*.zip' \
  --exclude '**/preview/*'
```

### Tune resumable range downloading

```bash
quarkdl download 'https://pan.quark.cn/s/your-share-id' \
  -o downloads \
  --workers 32 \
  --chunk-size 1048576
```

Each large file is split into range chunks. Completed chunks are kept under `*.chunks/` until the file is assembled, so interrupted downloads can continue instead of starting over.

### Keep the temporary cloud folder

```bash
quarkdl download 'https://pan.quark.cn/s/your-share-id' -o downloads --keep-cloud
```

By default, the temporary folder is deleted from your Quark drive after the local files are verified. If deletion fails, delete the folder manually in Quark.

### List a share without downloading

```bash
quarkdl list 'https://pan.quark.cn/s/your-share-id'
```

## Safety notes

- Login happens through Quark's official web page and app QR flow.
- The tool stores browser login state locally in your Playwright profile directory.
- It does not print raw cookies, `stoken`, generated download URLs, or account credentials.
- Output state such as `.quarkdl-status.json` can contain filenames and sizes; keep it private if filenames are sensitive.
- Temporary cloud folders are named `quarkdl-<share-id>-<timestamp>` and are deleted by default.
- Files are considered complete only when their local byte size matches Quark metadata.

## Limitations

- This is an alpha tool built around observed Quark web/desktop endpoints; Quark may change them.
- The first QR login needs a graphical browser session. Headless servers may need X forwarding or a desktop environment.
- Download speed is still subject to Quark/CDN/account throttling.
- For very large shares, `--pick` can print many filenames; use `--include` / `--exclude` globs for faster filtering.

## Development

```bash
pip install -e '.[dev]'
ruff check .
pytest -q
```

## License

MIT
