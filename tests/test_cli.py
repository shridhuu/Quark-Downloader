from pathlib import Path

from quarkdl.api import ShareEntry
from quarkdl.cli import build_parser, roots_covering_file_paths


def test_download_command_defaults_are_simple_for_first_run():
    args = build_parser().parse_args(
        [
            "download",
            "https://pan.quark.cn/s/abc123",
        ]
    )

    assert args.command == "download"
    assert args.output == Path("downloads")
    assert args.workers == 32
    assert args.chunk_size == 1024 * 1024
    assert args.include == []
    assert args.exclude == []
    assert args.yes is False


def test_download_command_accepts_noninteractive_filters():
    args = build_parser().parse_args(
        [
            "download",
            "https://pan.quark.cn/s/abc123",
            "--yes",
            "--include",
            "**/*.zip",
            "--exclude",
            "**/preview/*",
            "-o",
            "out",
        ]
    )

    assert args.yes is True
    assert args.include == ["**/*.zip"]
    assert args.exclude == ["**/preview/*"]
    assert args.output == Path("out")


def test_download_command_supports_file_picker_flag():
    args = build_parser().parse_args(
        [
            "download",
            "https://pan.quark.cn/s/abc123",
            "--pick",
        ]
    )

    assert args.pick is True


def test_roots_covering_file_paths_selects_minimum_root_entries():
    roots = [
        ShareEntry(fid="folder", file_name="Folder", path="Folder", size=0, is_dir=True),
        ShareEntry(fid="loose", file_name="loose.txt", path="loose.txt", size=2, is_dir=False),
        ShareEntry(fid="other", file_name="Other", path="Other", size=0, is_dir=True),
    ]

    selected = roots_covering_file_paths(roots, {"Folder/a.zip", "loose.txt"})

    assert [entry.fid for entry in selected] == ["folder", "loose"]
