"""District-level address truncation (publish / judgment-online)."""

from __future__ import annotations

import pytest

from legal_redactor.entities import _district_address


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("江苏省苏州市昆山市玉山镇测试路88号", "江苏省苏州市昆山市"),
        ("浙江省杭州市建德市新安江街道测试路9号", "浙江省杭州市建德市"),
        ("福建省福州市福清市音西街道测试路6号", "福建省福州市福清市"),
        ("江苏省常州市溧阳市溧城镇测试路3号", "江苏省常州市溧阳市"),
        ("河南省郑州市测试路1号", "河南省郑州市"),
        ("吉林省延边朝鲜族自治州延吉市测试路1号", "吉林省延边朝鲜族自治州延吉市"),
        ("内蒙古自治区锡林郭勒盟锡林浩特市测试路5号", "内蒙古自治区锡林郭勒盟锡林浩特市"),
        ("广东省东莞市虎门镇测试路1号", "广东省东莞市"),
        ("北京市朝阳区测试路88号", "北京市朝阳区"),
        ("广东省广州市天河区测试路1号", "广东省广州市天河区"),
    ],
)
def test_district_address(address: str, expected: str):
    assert _district_address(address) == expected
