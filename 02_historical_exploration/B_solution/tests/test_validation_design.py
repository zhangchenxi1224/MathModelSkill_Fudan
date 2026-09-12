from pathlib import Path
import importlib.util
import pytest

spec=importlib.util.spec_from_file_location('validation_design_test',Path(__file__).resolve().parents[1]/'scripts/prepare_official_validation.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_variance_changes_budget_but_never_claims_capped_power():
    quiet=module.sample_plan([5000+i for i in range(60)])
    noisy=module.sample_plan([1000+200*i for i in range(60)])
    assert quiet['planned_total']==40
    assert noisy['planned_total']==60
    assert noisy['capped_below_requested_power']
    assert noisy['approximate_power_at_target']<.8
    assert quiet['approximate_power_at_target']>.8


def test_small_sample_is_not_given_fake_precision():
    with pytest.raises(ValueError,match='ten'):
        module.sample_plan([5000]*4)
