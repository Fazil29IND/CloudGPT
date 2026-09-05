import pytest

from tools.calculator import CalculatorTool


def test_calculator_and_cloud_cost_helpers():
    tool = CalculatorTool()
    assert tool.calculate("2 * (3 + 4)").result == 14
    estimate = tool.monthly_cost(0.1, instances=2)
    assert estimate.hourly == 0.2
    assert estimate.monthly == pytest.approx(146.0)
    assert tool.unit_convert(1024, "GB", "TB") == 1
    comparison = tool.compare_costs({"aws": 10.0, "gcp": 8.0, "azure": 12.0})
    assert comparison.cheapest == "gcp"
    assert comparison.differences["aws"] == 2


def test_calculator_rejects_code_and_invalid_units():
    tool = CalculatorTool()
    with pytest.raises(ValueError):
        tool.calculate("__import__('os').system('echo unsafe')")
    with pytest.raises(ValueError):
        tool.unit_convert(1, "GB", "mile")
