"""Command-builder tests — pure builders, nothing is executed."""

from __future__ import annotations

import pytest

from beerus.commands import (
    TEMP_PASSWORD,
    ata_secure_erase_commands,
    nvme_format_command,
    nvme_sanitize_command,
    nvme_sanitize_log_command,
)


def test_ata_secure_erase_exact_argv():
    dp = "/dev/sdb"
    assert ata_secure_erase_commands(dp) == [
        ["hdparm", "--user-master", "u", "--security-set-pass", TEMP_PASSWORD, dp],
        ["hdparm", "--user-master", "u", "--security-erase", TEMP_PASSWORD, dp],
    ]
    assert TEMP_PASSWORD == "beerus"


def test_nvme_sanitize_exact_argv():
    assert nvme_sanitize_command("/dev/nvme0n1") == [
        "nvme",
        "sanitize",
        "/dev/nvme0n1",
        "--sanact=2",
    ]


def test_nvme_format_exact_argv():
    assert nvme_format_command("/dev/nvme0n1") == [
        "nvme",
        "format",
        "/dev/nvme0n1",
        "--ses=1",
    ]


def test_nvme_sanitize_log_exact_argv():
    assert nvme_sanitize_log_command("/dev/nvme0n1") == [
        "nvme",
        "sanitize-log",
        "/dev/nvme0n1",
    ]


ALL_BUILDERS = [
    ata_secure_erase_commands,
    nvme_sanitize_command,
    nvme_format_command,
    nvme_sanitize_log_command,
]


@pytest.mark.parametrize(
    "bad_path",
    ["sdb", "/dev/sda 1", "", "/dev/", "/dev/sda\t", " /dev/sda", None, 42],
)
@pytest.mark.parametrize("builder", ALL_BUILDERS)
def test_builders_reject_bad_device_paths(builder, bad_path):
    with pytest.raises(ValueError):
        builder(bad_path)


@pytest.mark.parametrize(
    "bad_path",
    ["/dev/../dev/sda", "/dev/../tmp/evil", "/dev/./sda", "/dev/sda\x00"],
)
@pytest.mark.parametrize("builder", ALL_BUILDERS)
def test_builders_reject_traversal_and_null_paths(builder, bad_path):
    with pytest.raises(ValueError):
        builder(bad_path)


@pytest.mark.parametrize("good_path", ["/dev/sda", "/dev/nvme0n1"])
@pytest.mark.parametrize("builder", ALL_BUILDERS)
def test_builders_accept_plain_device_paths(builder, good_path):
    result = builder(good_path)
    assert result  # a non-empty argv (or list of argv lists)
