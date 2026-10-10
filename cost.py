"""FinOps: estimate what each run costs and what bad data wastes. Rates are configurable."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    inr_per_vcpu_hour: float = 3.4       # list-price-like blended cost of a small cloud vCPU
    vcpus: int = 4
    overhead_seconds: float = 20.0       # cluster / job start-up amortised per run
    inr_per_gb_month: float = 1.7        # object storage
    bytes_per_row: int = 160

    def run_cost(self, compute_seconds: float, rows: int) -> dict[str, float]:
        compute = (compute_seconds + self.overhead_seconds) / 3600.0 * self.vcpus * self.inr_per_vcpu_hour
        storage = rows * self.bytes_per_row / 1e9 * self.inr_per_gb_month
        total = compute + storage
        return {"compute_inr": round(compute, 4), "storage_inr": round(storage, 6), "total_inr": round(total, 4)}

    def wasted(self, total_inr: float, rows_in: int, rows_quarantined: int, blocked: bool) -> float:
        """Spend that produced no usable data: the quarantined share (everything if blocked)."""
        if rows_in <= 0:
            return 0.0
        share = 1.0 if blocked else rows_quarantined / rows_in
        return round(total_inr * share, 4)
