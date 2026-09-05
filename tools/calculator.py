import ast
import operator

from pydantic import BaseModel


class CalculationResult(BaseModel):
    expression: str
    result: float
    steps: list[str]


class CostEstimate(BaseModel):
    hourly: float
    daily: float
    monthly: float
    yearly: float
    assumptions: str


class CostComparison(BaseModel):
    differences: dict[str, float]
    cheapest: str
    most_expensive: str


class CalculatorTool:
    """Safe evaluation and cloud cost calculation tool."""

    _operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _eval(self, node: ast.AST) -> float:
        """Safely evaluate AST node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        elif isinstance(node, ast.BinOp):
            return self._operators[type(node.op)](self._eval(node.left), self._eval(node.right))
        elif isinstance(node, ast.UnaryOp):
            return self._operators[type(node.op)](self._eval(node.operand))
        else:
            raise TypeError(f"Unsupported mathematical operation: {type(node).__name__}")

    def calculate(self, expression: str) -> CalculationResult:
        """Safely evaluates a mathematical expression string."""
        steps = [f"Evaluating: {expression}"]
        try:
            # Parse to AST and restrict to valid expression
            node = ast.parse(expression, mode='eval').body
            result = self._eval(node)
            steps.append(f"Result: {result}")
            return CalculationResult(expression=expression, result=float(result), steps=steps)
        except Exception as e:
            raise ValueError(f"Failed to calculate '{expression}': {e}")

    def monthly_cost(self, hourly_rate: float, hours_per_month: int = 730, instances: int = 1) -> CostEstimate:
        """Calculate cloud costs over time periods."""
        hourly = hourly_rate * instances
        daily = hourly * 24
        monthly = hourly * hours_per_month
        yearly = daily * 365

        assumptions = f"{instances} instance(s) running {hours_per_month} hours/month."

        return CostEstimate(
            hourly=hourly,
            daily=daily,
            monthly=monthly,
            yearly=yearly,
            assumptions=assumptions
        )

    def compare_costs(self, costs: dict[str, float]) -> CostComparison:
        """Compare provider costs."""
        if not costs:
            raise ValueError("No costs provided for comparison.")

        cheapest = min(costs, key=costs.get) # type: ignore
        most_expensive = max(costs, key=costs.get) # type: ignore

        differences = {}
        cheapest_val = costs[cheapest]
        for prov, cost in costs.items():
            if prov != cheapest:
                differences[prov] = cost - cheapest_val

        return CostComparison(
            differences=differences,
            cheapest=cheapest,
            most_expensive=most_expensive
        )

    def unit_convert(self, value: float, from_unit: str, to_unit: str) -> float:
        """Convert units commonly used in cloud sizing."""
        conversions = {
            ("GB", "TB"): value / 1024,
            ("TB", "GB"): value * 1024,
            ("MB", "GB"): value / 1024,
            ("GB", "MB"): value * 1024,
            ("seconds", "hours"): value / 3600,
            ("hours", "seconds"): value * 3600,
            ("hours", "days"): value / 24,
            ("days", "hours"): value * 24,
        }

        key = (from_unit, to_unit)
        if key in conversions:
            return conversions[key]

        raise ValueError(f"Unsupported unit conversion from {from_unit} to {to_unit}")
