"""Phân hệ giải thuật: Đa đồ thị có hướng (MultiDiGraph), Định tuyến hỗn hợp (Uniswap V2 + Curve StableSwap)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

SLIPPAGE_TOLERANCE = 0.002  # 0.2%


@dataclass
class Hop:
    token_in: str
    token_out: str
    pool_name: str
    pool_address: str
    amm_type: str          # UNISWAP_V2 | CURVE_STABLE
    amount_in: float
    amount_out: float
    spot_rate: float       # Tỷ giá cận biên đã trừ phí
    effective_rate: float  # Tỷ giá thực nhận (amount_out / amount_in)

    @property
    def price_impact(self) -> float:
        return 1.0 - self.effective_rate / self.spot_rate if self.spot_rate else 0.0


@dataclass
class Opportunity:
    cycle: list[str]
    spot_return: float          # Tích tỷ giá spot quanh chu trình
    optimal_amount_in: float
    expected_output: float
    max_net_profit: float
    min_total_return: float
    verdict: str                # EXECUTE | ABORT
    hits_upper_bound: bool
    capital_range: np.ndarray = field(repr=False, default=None)
    profits: np.ndarray = field(repr=False, default=None)
    hops: list[Hop] = field(default_factory=list)


class ArbitrageEngine:
    def __init__(self, gas_cost_native: float = 0.006):
        self.gas_cost = gas_cost_native
        self.graph = nx.MultiDiGraph()

    # -------------------------------------------------------------------------
    def build_graph(self, pools: list[dict]):
        """Nạp các Pool vào Đa đồ thị, nhận diện kiểu AMM (Uniswap V2 hoặc Curve StableSwap)."""
        self.graph.clear()
        for p in pools:
            r0, r1 = float(p["reserve0"]), float(p["reserve1"])
            fee = float(p.get("fee", 0.003))
            amm_type = p.get("amm_type", "UNISWAP_V2")
            amp_a = float(p.get("A", 100.0))

            if r0 <= 0 or r1 <= 0:
                continue

            for a, b, r_in, r_out in ((p["token0"], p["token1"], r0, r1),
                                      (p["token1"], p["token0"], r1, r0)):
                spot = (r_out / r_in) * (1.0 - fee)
                
                # Gán đầy đủ thuộc tính bao gồm 'spot' và 'amm_type'
                self.graph.add_edge(
                    a, b,
                    key=p["pool_address"],
                    weight=-math.log(spot),
                    spot=spot,
                    pool=p["pool_address"],
                    pool_name=p.get("pool_name", p["pool_address"]),
                    amm_type=amm_type,
                    A=amp_a,
                    r_in=r_in,
                    r_out=r_out,
                    fee=fee
                )

    # -------------------------------------------------------------------------
    @staticmethod
    def get_amount_out_uniswap_v2(amount_in: float, r_in: float, r_out: float, fee: float) -> float:
        """Công thức sản số x * y = k chuẩn của Uniswap V2."""
        dx = amount_in * (1.0 - fee)
        return r_out * dx / (r_in + dx)

    @staticmethod
    def get_amount_out_curve_stableswap(amount_in: float, r_in: float, r_out: float, fee: float, A: float = 100.0) -> float:
        """Mô phỏng trượt giá Curve StableSwap dựa trên tham số A."""
        dx = amount_in * (1.0 - fee)
        damping_factor = 1.0 / (1.0 + (dx / r_in) * (100.0 / A))
        slippage_v2 = (r_out * dx) / (r_in + dx)
        ideal_out = dx * (r_out / r_in)
        return ideal_out - (ideal_out - slippage_v2) * damping_factor

    def calculate_amount_out(self, amount_in: float, edge: dict) -> float:
        """Router điều hướng tính toán dựa trên thuộc tính amm_type của cạnh."""
        if edge.get("amm_type") == "CURVE_STABLE":
            return self.get_amount_out_curve_stableswap(
                amount_in, edge["r_in"], edge["r_out"], edge["fee"], edge.get("A", 100.0)
            )
        else:
            return self.get_amount_out_uniswap_v2(
                amount_in, edge["r_in"], edge["r_out"], edge["fee"]
            )

    # -------------------------------------------------------------------------
    def get_best_edge(self, u: str, v: str) -> dict:
        """Lấy cạnh (pool) có tỷ giá spot tốt nhất trong MultiDiGraph."""
        edges = self.graph[u][v]
        best_key = min(edges, key=lambda k: edges[k]["weight"])
        return edges[best_key]

    def cycle_weight(self, cycle: list[str]) -> float:
        return sum(self.get_best_edge(u, v)["weight"] for u, v in zip(cycle, cycle[1:]))

    # -------------------------------------------------------------------------
    def detect_cycle(self) -> list[str] | None:
        """Bellman-Ford quét chu trình âm trên Đa đồ thị."""
        for src in self.graph.nodes():
            try:
                cycle = nx.find_negative_cycle(self.graph, source=src, weight="weight")
            except (nx.NetworkXError, IndexError):
                continue
            if cycle[0] != cycle[-1]:
                cycle.append(cycle[0])
            return cycle
        return None

    def candidate_cycles(self, base_token: str, max_hops: int = 4) -> list[list[str]]:
        """Duyệt DFS tìm mọi chu trình đơn khép kín đi qua base_token."""
        if base_token not in self.graph:
            return []
        found: list[list[str]] = []

        def dfs(path: list[str], visited: set[str]):
            u = path[-1]
            for v in self.graph.successors(u):
                if v == base_token and len(path) >= 3:
                    cyc = path + [base_token]
                    if self.cycle_weight(cyc) < 0:
                        found.append(cyc)
                elif v not in visited and len(path) < max_hops:
                    visited.add(v)
                    dfs(path + [v], visited)
                    visited.remove(v)

        dfs([base_token], {base_token})
        return found

    # -------------------------------------------------------------------------
    def simulate_path(self, cycle: list[str], amount_in):
        amt = amount_in
        for u, v in zip(cycle, cycle[1:]):
            e = self.get_best_edge(u, v)
            amt = self.calculate_amount_out(amt, e)
        return amt

    def trace_path(self, cycle: list[str], amount_in: float) -> list[Hop]:
        """Tạo chi tiết danh sách từng hop (Đã fix lỗi KeyError 'spot')."""
        hops, amt = [], amount_in
        for u, v in zip(cycle, cycle[1:]):
            e = self.get_best_edge(u, v)
            out = float(self.calculate_amount_out(amt, e))
            
            # TRUY XUẤT SPOT AN TOÀN VỚI .get()
            spot_val = e.get("spot", (e["r_out"] / e["r_in"]) * (1.0 - e.get("fee", 0.003)))
            
            hops.append(Hop(
                u, v,
                e.get("pool_name", e.get("pool", "Pool")),
                e.get("pool", "0x"),
                e.get("amm_type", "UNISWAP_V2"),
                amt, out, spot_val, out / amt if amt else 0.0
            ))
            amt = out
        return hops

    def _closed_form(self, cycle: list[str]) -> tuple[float, float, float]:
        A, B, C = 1.0, 1.0, 0.0
        for u, v in zip(cycle, cycle[1:]):
            e = self.get_best_edge(u, v)
            g = 1.0 - e["fee"]
            A, B, C = e["r_out"] * g * A, e["r_in"] * B, e["r_in"] * C + g * A
        return A, B, C

    # -------------------------------------------------------------------------
    def optimize_capital(self, cycle: list[str], max_capital: float = 15.0, steps: int = 1000) -> Opportunity:
        steps = max(steps, 500)
        x_min = max_capital * 0.001
        xs = np.linspace(x_min, max_capital, steps)
        profits = self.simulate_path(cycle, xs) - xs - self.gas_cost

        A, B, C = self._closed_form(cycle)
        x_star = (math.sqrt(A * B) - B) / C if A > B and C > 0 else x_min
        x_star = min(max(x_star, x_min), max_capital)

        x_scan = float(xs[int(np.argmax(profits))])
        if self.simulate_path(cycle, x_scan) - x_scan > self.simulate_path(cycle, x_star) - x_star:
            x_star = x_scan

        out = float(self.simulate_path(cycle, x_star))
        net = out - x_star - self.gas_cost
        ok = net > 0

        return Opportunity(
            cycle=cycle,
            spot_return=math.exp(-self.cycle_weight(cycle)),
            optimal_amount_in=x_star,
            expected_output=out,
            max_net_profit=net,
            min_total_return=out * (1.0 - SLIPPAGE_TOLERANCE) if ok else 0.0,
            verdict="EXECUTE" if ok else "ABORT",
            hits_upper_bound=bool(x_star >= max_capital * 0.999),
            capital_range=xs, profits=profits,
            hops=self.trace_path(cycle, x_star),
        )

    def find_best(self, base_token: str, max_capital: float, steps: int = 1000, max_hops: int = 4):
        raw = self.detect_cycle()
        if raw is None:
            return None, [], None
        opps = [self.optimize_capital(c, max_capital, steps) for c in self.candidate_cycles(base_token, max_hops)]
        opps.sort(key=lambda o: o.max_net_profit, reverse=True)
        return (opps[0] if opps else None), opps, raw