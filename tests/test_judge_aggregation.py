from __future__ import annotations

import pytest

from app.engine.judge_aggregation import (
    aggregate_judges,
    aggregate_judges_trimmed,
    analyze_dispersion,
    predicted_mean_from_base,
)

# 真实评委分（评标一览表实测）
CHANGCHUN = [4.33, 4.36, 4.35, 4.36, 4.8]  # 运康骨科 长春建设 -> 4.44
LUJIN = [82.06, 82.09, 82.06, 82.08, 82.12, 82.09, 82.18]  # 包河道路 庐金(7评委) -> 82.10
KAIYANG = [4.18, 4.21, 4.23, 4.21, 4.67]  # 运康骨科 凯扬 -> 4.30


def test_simple_mean_matches_real_results():
    assert aggregate_judges(CHANGCHUN) == 4.44
    assert aggregate_judges(LUJIN) == 82.10
    assert aggregate_judges(KAIYANG) == 4.30


def test_trimmed_differs_from_simple_mean():
    # 真实用简单平均；去最高最低会得到不同结果，证明不能用 trimmed 替代
    assert aggregate_judges_trimmed(CHANGCHUN) != aggregate_judges(CHANGCHUN)


def test_dispersion_separates_base_and_attention():
    d = analyze_dispersion(CHANGCHUN)  # tol=0.1
    assert d.mean == 4.44
    assert d.base_estimate == 4.36  # 中位数=基准（抗宽松评委）
    assert d.most_deviant_index == 4  # 评委5（4.8）调了关注度
    assert d.most_deviant_value == 4.8
    assert d.n_consensus == 4  # 4 位未调（与基准在0.1内）
    assert d.n_adjusted == 1


def test_dispersion_tight_cluster_high_consensus():
    d = analyze_dispersion(LUJIN)
    assert d.n == 7
    assert d.spread <= 0.2  # 7 位高度一致
    assert d.most_deviant_value == 82.18


def test_predicted_mean_from_base():
    # 多数评委不调关注度 -> 均分≈基准
    assert predicted_mean_from_base(4.36) == 4.36
    # 给出各评委偏移则按均值重建
    assert predicted_mean_from_base(4.36, adjustment_offsets=[0, 0, 0, 0, 0.44]) == 4.45


def test_empty_raises():
    with pytest.raises(ValueError):
        aggregate_judges([])
    with pytest.raises(ValueError):
        analyze_dispersion([])
