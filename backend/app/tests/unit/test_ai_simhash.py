from __future__ import annotations

from app.modules.ai.simhash import compute_simhash, hamming_distance, simhash_bands


def test_simhash_is_stable_for_identical_text() -> None:
    text = "knowledge uploader handbook review workflow " * 20

    assert compute_simhash(text) == compute_simhash(text)


def test_small_text_change_keeps_hamming_distance_low() -> None:
    base = "knowledge uploader handbook review workflow ragflow sync quality gate " * 30
    changed = base + "minor appendix"

    assert hamming_distance(compute_simhash(base), compute_simhash(changed)) <= 3


def test_unrelated_text_has_larger_hamming_distance() -> None:
    left = "knowledge uploader handbook review workflow ragflow sync quality gate " * 20
    right = "财务 报销 发票 审批 预算 科目 银行 对账 月结 季度 付款 " * 20

    assert hamming_distance(compute_simhash(left), compute_simhash(right)) > 3


def test_simhash_bands_are_signed_sixteen_bit_values() -> None:
    bands = simhash_bands(compute_simhash("alpha beta gamma delta" * 10))

    assert len(bands) == 4
    assert all(-32768 <= band <= 32767 for band in bands)
