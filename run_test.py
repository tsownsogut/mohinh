import time
from reader import OnChainReader
from engine import ArbitrageEngine

def main():
    base = "WETH"
    pools = OnChainReader.get_mock_pools(dai_price=2410, active_stable="USDT")
    t0 = time.perf_counter()
    eng = ArbitrageEngine(gas_cost_native=0.008)
    eng.build_graph(pools)
    best, opps, raw = eng.find_best(base, max_capital=12.0)
    dt = (time.perf_counter() - t0) * 1000
    print(f"Đồ thị: {eng.graph.number_of_nodes()} token, {eng.graph.number_of_edges()} cạnh | {dt:.1f} ms")
    if raw is None:
        print("Thị trường cân bằng - không có arbitrage."); return
    if best is None:
        print(f"Có chu trình âm {raw} nhưng không đi qua {base}."); return
    for o in opps:
        print(f"{' -> '.join(o.cycle):38s} spot x{o.spot_return:.4f} | Δx*={o.optimal_amount_in:.4f} "
              f"| lãi ròng={o.max_net_profit:.4f} | {o.verdict}")
    print("Slippage guard:", round(best.min_total_return, 4), base)

if __name__ == "__main__":
    main()