import math
import numpy as np
import networkx as nx
import matplotlib.pyplot as plt

# ==============================================================================
# MODULE 1: ĐỊNH NGHĨA POOL & HÀM BẢO TOÀN AMM (x * y = k)
# ==============================================================================
class AMMPool:
    def __init__(self, name, token_a, token_b, reserve_a, reserve_b, fee=0.003):
        self.name = name
        self.token_a = token_a
        self.token_b = token_b
        self.reserve_a = float(reserve_a)
        self.reserve_b = float(reserve_b)
        self.fee = float(fee)

    def get_spot_rate(self, token_in):
        """Tỷ giá cận biên lý thuyết (spot rate) sau khi trừ phí giao dịch"""
        if token_in == self.token_a:
            return (self.reserve_b / self.reserve_a) * (1.0 - self.fee)
        elif token_in == self.token_b:
            return (self.reserve_a / self.reserve_b) * (1.0 - self.fee)
        raise ValueError(f"Token {token_in} không nằm trong pool {self.name}")

    def swap(self, token_in, amount_in):
        """Tính token đầu ra thực tế theo công thức AMM có tính trượt giá phi tuyến:
           dy = (y * dx * (1 - f)) / (x + dx * (1 - f))
        """
        if token_in == self.token_a:
            r_in, r_out = self.reserve_a, self.reserve_b
        elif token_in == self.token_b:
            r_in, r_out = self.reserve_b, self.reserve_a
        else:
            raise ValueError(f"Token {token_in} không hợp lệ")

        amount_in_effective = amount_in * (1.0 - self.fee)
        amount_out = (r_out * amount_in_effective) / (r_in + amount_in_effective)
        return amount_out


# ==============================================================================
# MODULE 2: KHỞI TẠO MẠNG LƯỚI & XÂY DỰNG ĐỒ THỊ NETWORKX
# ==============================================================================
def build_arbitrage_graph(pools):
    """Ánh xạ các Pool thành đồ thị có hướng DiGraph với trọng số w = -ln(R_spot)"""
    G = nx.DiGraph()

    for p in pools:
        # Hướng Token A -> Token B
        rate_ab = p.get_spot_rate(p.token_a)
        G.add_edge(
            p.token_a, p.token_b,
            weight=-math.log(rate_ab),
            spot_rate=rate_ab,
            pool=p
        )

        # Hướng Token B -> Token A
        rate_ba = p.get_spot_rate(p.token_b)
        G.add_edge(
            p.token_b, p.token_a,
            weight=-math.log(rate_ba),
            spot_rate=rate_ba,
            pool=p
        )

    return G


# ==============================================================================
# MODULE 3: THUẬT TOÁN QUÉT CHU TRÌNH ÂM (BELLMAN-FORD)
# ==============================================================================
def detect_arbitrage_cycle(graph, source_token="WETH"):
    """Dùng Bellman-Ford tìm chu trình có tổng trọng số âm"""
    try:
        cycle = nx.find_negative_cycle(graph, source=source_token, weight="weight")
        return cycle
    except nx.NetworkXNoCycle:
        return None


# ==============================================================================
# MODULE 4: MÔ PHỎNG TRƯỢT GIÁ & TỐI ƯU HÓA QUY MÔ VỐN (Δx*)
# ==============================================================================
def simulate_cycle_execution(graph, cycle, amount_in):
    """Mô phỏng bơm vốn qua từng pool trong chu trình để tính sản lượng thu về"""
    current_amount = amount_in
    for i in range(len(cycle) - 1):
        u, v = cycle[i], cycle[i+1]
        pool = graph[u][v]["pool"]
        current_amount = pool.swap(token_in=u, amount_in=current_amount)
    return current_amount


def optimize_capital(graph, cycle, min_cap=0.01, max_cap=8.0, steps=400, gas_cost=0.002):
    """Quét dải vốn đầu vào để tìm điểm cực trị lợi nhuận sau khi trừ phí gas"""
    capitals = np.linspace(min_cap, max_cap, steps)
    net_profits = []

    for cap in capitals:
        amt_out = simulate_cycle_execution(graph, cycle, cap)
        profit = amt_out - cap - gas_cost
        net_profits.append(profit)

    net_profits = np.array(net_profits)
    best_idx = np.argmax(net_profits)

    best_capital = capitals[best_idx]
    max_profit = net_profits[best_idx]

    return capitals, net_profits, best_capital, max_profit


# ==============================================================================
# MODULE 5: TRỰC QUAN HÓA MÔ HÌNH VÀ KẾT QUẢ PHÂN TÍCH
# ==============================================================================
def visualize_results(graph, cycle, capitals, net_profits, best_capital, max_profit, gas_cost):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))

    # --- Biểu đồ 1: Cấu trúc Đồ thị Mạng lưới ---
    pos = nx.spring_layout(graph, seed=42)
    cycle_edges = list(zip(cycle[:-1], cycle[1:])) if cycle else []

    edge_colors = ['red' if (u, v) in cycle_edges else '#cccccc' for u, v in graph.edges()]
    edge_widths = [2.5 if (u, v) in cycle_edges else 1.0 for u, v in graph.edges()]

    nx.draw_networkx_nodes(graph, pos, ax=ax1, node_color='#b3e5fc', node_size=2200, edgecolors='black')
    nx.draw_networkx_labels(graph, pos, ax=ax1, font_size=11, font_weight='bold')
    nx.draw_networkx_edges(
        graph, pos, ax=ax1,
        edge_color=edge_colors,
        width=edge_widths,
        arrowsize=18,
        connectionstyle="arc3,rad=0.1"
    )
    ax1.set_title("1. Bản đồ Mạng lưới Pool & Chu trình Arbitrage (Đỏ)", fontsize=11, fontweight='bold')
    ax1.axis("off")

    # --- Biểu đồ 2: Đường cong Trượt giá & Lợi nhuận ròng ---
    ax2.plot(capitals, net_profits, color='#2e7d32', linewidth=2.2, label="Lợi nhuận ròng (Net Profit)")
    ax2.axhline(0, color='black', linestyle='--', linewidth=0.8, alpha=0.7)
    ax2.axvline(best_capital, color='red', linestyle=':', label=f"Vốn tối ưu: {best_capital:.2f} WETH")
    ax2.scatter([best_capital], [max_profit], color='red', s=60, zorder=5)

    ax2.set_xlabel("Vốn nạp ban đầu $\\Delta x$ (WETH)", fontsize=10)
    ax2.set_ylabel("Lợi nhuận ròng sau Gas (WETH)", fontsize=10)
    ax2.set_title(f"2. Tác động của Trượt giá (Gas = {gas_cost} ETH)", fontsize=11, fontweight='bold')
    ax2.grid(True, linestyle=":", alpha=0.6)
    ax2.legend(loc="upper right")

    plt.tight_layout()
    plt.savefig("arbitrage_engine_output.png", dpi=300)
    plt.close()
    print("-> Đã lưu trực quan hóa vào file: arbitrage_engine_output.png")


# ==============================================================================
# MAIN EXECUTION PIPELINE
# ==============================================================================
if __name__ == "__main__":
    print("=== KHỞI CHẠY HỆ THỐNG PHÂN TÍCH ARBITRAGE OFF-CHAIN ===")

    # Thiết lập bộ dữ liệu pool mô phỏng (tạo chênh lệch giá có chủ đích)
    market_pools = [
        # Uniswap V3: 1 WETH = 3,100 USDC (Pool sâu thanh khoản)
        AMMPool("UniV3_WETH_USDC", "WETH", "USDC", reserve_a=150.0, reserve_b=465000.0, fee=0.003),

        # Curve: 1 USDC = 1.002 DAI (Bể stablecoin)
        AMMPool("Curve_USDC_DAI",  "USDC", "DAI",  reserve_a=600000.0, reserve_b=601200.0, fee=0.0004),

        # Sushiswap: 1 WETH = 3,040 DAI (Pool lệch giá: DAI đổi được nhiều WETH hơn)
        AMMPool("Sushi_WETH_DAI",  "WETH", "DAI",  reserve_a=70.0,  reserve_b=212800.0, fee=0.003),

        # Các pool vệ tinh bổ trợ cấu trúc mạng lưới
        AMMPool("UniV3_WETH_UNI",  "WETH", "UNI",  reserve_a=40.0,  reserve_b=7600.0,   fee=0.003),
        AMMPool("UniV3_UNI_USDC",  "UNI",  "USDC",  reserve_a=9000.0, reserve_b=157500.0, fee=0.003)
    ]

    # 1. Dựng đồ thị
    graph = build_arbitrage_graph(market_pools)
    print(f"[1] Dựng đồ thị thành công: {graph.number_of_nodes()} Token, {graph.number_of_edges()} Cặp cạnh.")

    # 2. Quét chu trình âm
    cycle = detect_arbitrage_cycle(graph, source_token="WETH")

    if cycle:
        print(f"[2] Phát hiện chu trình Arbitrage tiềm năng: {' -> '.join(cycle)}")

        # 3. Mô phỏng trượt giá và tìm vốn tối ưu
        gas_cost_estimate = 0.002
        caps, profits, opt_cap, max_p = optimize_capital(
            graph, cycle, min_cap=0.05, max_cap=6.0, steps=300, gas_cost=gas_cost_estimate
        )

        print("\n[3] KẾT QUẢ TỐI ƯU HÓA QUY MÔ VỐN:")
        print(f"    - Vốn đầu vào tối ưu (Δx*): {opt_cap:.3f} WETH")
        print(f"    - Lợi nhuận ròng cực đại:    {max_p:.4f} WETH (sau khi trừ {gas_cost_estimate} ETH Gas)")

        if max_p > 0:
            print("    => QUYẾT ĐỊNH: Cơ hội sinh lời thực tế hợp lệ. Đủ điều kiện kích hoạt Smart Contract.")
        else:
            print("    => QUYẾT ĐỊNH: Lỗ ròng do Gas/Trượt giá. Hủy lệnh, không gửi giao dịch.")

        # 4. Xuất đồ thị
        visualize_results(graph, cycle, caps, profits, opt_cap, max_p, gas_cost_estimate)

    else:
        print("[2] Thị trường cân bằng, không tồn tại chu trình chênh lệch giá.")