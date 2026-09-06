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


def _safe_pow(base: float, exp: float) -> float:
    """Safe exponentiation preventing CPU denial of service and memory exhaustion."""
    if abs(exp) > 1000:
        raise ValueError(f"Exponent {exp} exceeds safe calculation limit of 1000")
    if abs(base) > 1e12 and exp > 1:
        raise ValueError(f"Base {base} too large for exponentiation")
    try:
        res = operator.pow(base, exp)
        if isinstance(res, (int, float)):
            if res == float("inf") or res == float("-inf") or res != res:
                raise OverflowError("Exponentiation overflow")
        return float(res)
    except (OverflowError, ValueError) as err:
        raise ValueError(f"Exponentiation out of safe numerical bounds: {err}")


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("Division by zero")
    return operator.truediv(a, b)


def _safe_mod(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("Modulo by zero")
    return operator.mod(a, b)


class CalculatorTool:
    """Safe evaluation and cloud cost calculation tool."""

    _operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: _safe_div,
        ast.Pow: _safe_pow,
        ast.Mod: _safe_mod,
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
        """Safely evaluates a mathematical expression string with DoS protection."""
        if not expression or not expression.strip():
            raise ValueError("Empty expression")
        if len(expression) > 500:
            raise ValueError("Expression exceeds maximum allowed length of 500 characters")

        steps = [f"Evaluating: {expression}"]
        try:
            # Parse to AST and restrict to valid expression
            parsed = ast.parse(expression, mode='eval')
            node = parsed.body

            # Complexity guard: cap AST nodes to prevent deeply nested or recursive bombs
            node_count = sum(1 for _ in ast.walk(parsed))
            if node_count > 50:
                raise ValueError("Expression complexity exceeds maximum allowed AST node limit")

            result = self._eval(node)
            if abs(result) > 1e100 or result != result:
                raise ValueError("Calculation result exceeds numerical limits")

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
