from pathlib import Path

import pytest

from quarkdl.core import (
    DownloadItem,
    human_bytes,
    parse_share_url,
    plan_ranges,
    sanitize_rel_path,
    select_items,
)


def test_parse_share_url_extracts_pwd_id_and_passcode():
    parsed = parse_share_url("https://pan.quark.cn/s/sample123#/list/share?pwd=abcd")
    assert parsed.pwd_id == "sample123"
    assert parsed.passcode == "abcd"


def test_parse_share_url_accepts_url_without_passcode():
    parsed = parse_share_url("https://pan.quark.cn/s/abcDEF123")
    assert parsed.pwd_id == "abcDEF123"
    assert parsed.passcode == ""


def test_parse_share_url_rejects_non_quark_links():
    with pytest.raises(ValueError, match="Quark share"):
        parse_share_url("https://example.com/s/sample123")


def test_sanitize_rel_path_strips_temp_parent_and_blocks_traversal():
    rel = sanitize_rel_path(
        "Temp/../quarkdl-temp-123/root/..//folder\\file.zip", temp_parent="quarkdl-temp-123"
    )
    assert rel == Path("Temp/root/folder/file.zip")


def test_sanitize_rel_path_removes_temp_parent_prefix():
    rel = sanitize_rel_path("quarkdl-temp-123/root/file.zip", temp_parent="quarkdl-temp-123")
    assert rel == Path("root/file.zip")


def test_plan_ranges_covers_file_without_overlap():
    assert plan_ranges(10, 4) == [(0, 3), (4, 7), (8, 9)]


def test_plan_ranges_rejects_invalid_values():
    with pytest.raises(ValueError):
        plan_ranges(-1, 4)
    with pytest.raises(ValueError):
        plan_ranges(10, 0)


def test_select_items_supports_include_and_exclude_globs():
    items = [
        DownloadItem(fid="1", path="root/a.zip", size=1, is_dir=False),
        DownloadItem(fid="2", path="root/b.rar", size=1, is_dir=False),
        DownloadItem(fid="3", path="root/note.txt", size=1, is_dir=False),
    ]
    selected = select_items(items, include=["**/*.*"], exclude=["**/*.txt", "**/*.rar"])
    assert [item.path for item in selected] == ["root/a.zip"]


def test_human_bytes_uses_binary_units():
    assert human_bytes(1024) == "1.0 KiB"
    assert human_bytes(5 * 1024 * 1024) == "5.0 MiB"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('root/a:b?c*d"e<f>g|h.txt', "root/a_b_c_d_e_f_g_h.txt"),
        ("root/name. /file.txt", "root/name/file.txt"),
        ("root/CON/aux.txt/nul", "root/_CON/_aux.txt/_nul"),
        ("root/com1.tar.gz", "root/_com1.tar.gz"),
        ("root/console.txt", "root/console.txt"),
    ],
)
def test_sanitize_rel_path_makes_names_windows_safe(raw, expected):
    assert sanitize_rel_path(raw) == Path(expected)


def test_sanitize_rel_path_rejects_names_that_vanish():
    with pytest.raises(ValueError, match="no usable file name"):
        sanitize_rel_path("...")
