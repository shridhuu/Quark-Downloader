
import pytest

from quarkdl.core import DownloadItem
from quarkdl.downloader import ResumableDownloader, _assemble


class FakeApi:
    def __init__(self):
        self.calls = 0

    def get_download_info(self, fid: str):
        self.calls += 1
        return {"download_url": "https://example.invalid/file"}


def test_completed_file_is_skipped_without_requesting_download_url(tmp_path):
    api = FakeApi()
    out = tmp_path / "out"
    dest = out / "folder" / "file.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"abc")
    item = DownloadItem(fid="fid1", path="folder/file.bin", size=3, is_dir=False, name="file.bin")

    downloader = ResumableDownloader(api, out)
    result = downloader.download_one(item)

    assert result == dest
    assert api.calls == 0


def test_assemble_rebuilds_chunks_in_order(tmp_path):
    chunks = tmp_path / "file.chunks"
    chunks.mkdir()
    (chunks / "000000.part").write_bytes(b"hello ")
    (chunks / "000001.part").write_bytes(b"world")
    part = tmp_path / "file.part"

    _assemble(chunks, part, [(0, 5), (6, 10)])

    assert part.read_bytes() == b"hello world"


def test_assemble_fails_on_missing_chunk(tmp_path):
    chunks = tmp_path / "file.chunks"
    chunks.mkdir()
    (chunks / "000000.part").write_bytes(b"hello ")

    with pytest.raises(RuntimeError, match="missing or invalid chunk"):
        _assemble(chunks, tmp_path / "file.part", [(0, 5), (6, 10)])
