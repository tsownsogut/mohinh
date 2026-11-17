"""Dashboard giám sát phân hệ Off-chain: DeFi Cyclical Arbitrage & Slippage Engine."""
import time

import networkx as nx
import numpy as np
import plotly.graph_objects as go
import streamlit as st

from engine import ArbitrageEngine
from reader import DEFAULT_POOLS, TOKENS, OnChainReader

GREEN, RED, AMBER, BLUE, GRAY = "#10b981", "#ef4444", "#f59e0b", "#3b82f6", "#94a3b8"
GRID = "rgba(128,128,128,0.18)"

st.set_page_config(page_title="DeFi Arbitrage Engine", page_icon="⚡", layout="wide")
st.markdown(
    """<style>
    .block-container {padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1400px;}
    [data-testid="stMetricValue"] {font-size: 1.6rem;}
    </style>""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------- helpers
def show(obj, key=None):
    try:
        st.plotly_chart(obj, width="stretch", key=key)
    except TypeError:
        st.plotly_chart(obj, use_container_width=True, key=key)


def table(df):
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except TypeError:
        st.dataframe(df, use_container_width=True, hide_index=True)


@st.cache_data(ttl=12, show_spinner="Đang đọc reserve on-chain…")
def load_live_pools(rpc_url: str):
    return OnChainReader(rpc_url).connect().get_live_pools()


def fmt(x, base):
    return f"{x:,.4f} {base}"


# ----------------------------------------------------------------- sidebar
sb = st.sidebar
sb.header("Cấu hình")

with sb.expander("Danh mục", expanded=True):
    base_token = st.selectbox("Token gốc (Base asset)", list(TOKENS), index=0,
                              help="Đồng tiền xuất phát và nhận lợi nhuận về ví.")
    target_stable = st.radio("Nhánh stablecoin ưu tiên", ["USDC", "USDT"], horizontal=True,
                             help="Chế độ Mock: chọn nhánh để ưu tiên tỷ giá.")

with sb.expander("Nguồn dữ liệu", expanded=False):
    source = st.radio("Chế độ", ["Mock", "RPC (Web3)"], horizontal=True)
    rpc_url = ""
    if source.startswith("RPC"):
        rpc_url = st.text_input("RPC URL", placeholder="https://eth-sepolia.g.alchemy.com/v2/…")
        if st.button("Làm mới dữ liệu"):
            load_live_pools.clear()

mock = source == "Mock"
with sb.expander("Thị trường (stress test)", expanded=True):
    dai_price = st.slider(
        "Giá 1 WETH trong pool DAI/WETH (DAI)", 2300.0, 2700.0, 2410.0, 10.0, disabled=not mock,
        help="Giá chuẩn ≈ 2500. Thấp hơn: WETH rẻ ở pool DAI → chu trình thuận. "
             "Cao hơn: WETH đắt ở pool DAI → chu trình đảo chiều. Vùng cân bằng ≈ 2477–2523.")
    liq_pct = st.slider("Độ sâu thanh khoản (%)", 10, 200, 100, 10, disabled=not mock,
                        help="Thanh khoản mỏng → trượt giá dốc hơn, đỉnh lợi nhuận dịch về vốn nhỏ.")

is_eth = base_token == "WETH"
with sb.expander("Chi phí & dải vốn", expanded=True):
    gas_fee = st.slider(f"Chi phí gas ({base_token})", 0.0, 0.15 if is_eth else 300.0,
                        0.008 if is_eth else 20.0, 0.002 if is_eth else 5.0,
                        format="%.4f" if is_eth else "%.1f", key=f"gas_{base_token}")
    max_cap = st.slider(f"Dải vốn quét tối đa ({base_token})", 0.5 if is_eth else 500.0,
                        40.0 if is_eth else 40000.0, 12.0 if is_eth else 15000.0,
                        0.5 if is_eth else 500.0, key=f"cap_{base_token}")
    max_hops = st.select_slider("Số hop tối đa của chu trình", [3, 4], value=4)

# ----------------------------------------------------------------- data + engine
try:
    if mock:
        pools = OnChainReader.get_mock_pools(dai_price, liq_pct / 100.0, target_stable)
    else:
        if not rpc_url:
            st.info("Nhập RPC URL ở thanh bên để đọc dữ liệu thật, hoặc chuyển về chế độ Mock.")
            st.stop()
        pools = load_live_pools(rpc_url)
except Exception as exc:  # noqa: BLE001
    st.error(f"Không đọc được dữ liệu pool: {exc}")
    st.stop()

t0 = time.perf_counter()
engine = ArbitrageEngine(gas_cost_native=gas_fee)
engine.build_graph(pools)
best, opps, raw_cycle = engine.find_best(base_token, max_cap, steps=1000, max_hops=max_hops)
latency_ms = (time.perf_counter() - t0) * 1000

# ----------------------------------------------------------------- header + status
head_l, head_r = st.columns([4, 1])
head_l.title("⚡ DeFi Arbitrage Engine")
head_l.caption("Phát hiện chu trình âm Bellman-Ford · Tối ưu vốn theo trượt giá AMM x·y=k")
head_r.metric("Độ trễ tính toán", f"{latency_ms:.1f} ms", help="Mục tiêu NFR-01: dưới 200 ms.")

if raw_cycle is None:
    st.success("**Thị trường cân bằng.** Không có chu trình âm (Σw ≥ 0), hệ thống không phát lệnh và không tốn gas.")
elif best is None:
    st.warning(f"Có chu trình chênh lệch **{' → '.join(raw_cycle)}** nhưng không đi qua **{base_token}**. "
               f"Hãy đổi token gốc sang một token nằm trong chu trình.")
elif best.verdict == "EXECUTE":
    st.success(f"**EXECUTE** · {' → '.join(best.cycle)} · lãi ròng dự kiến **{fmt(best.max_net_profit, base_token)}**")
else:
    st.error(f"**ABORT** · {' → '.join(best.cycle)} · gas và trượt giá triệt tiêu lợi nhuận "
             f"(lãi ròng cực đại {fmt(best.max_net_profit, base_token)})")

if best and best.hits_upper_bound:
    st.warning("Điểm tối ưu chạm biên dải vốn quét. Hãy tăng “Dải vốn quét tối đa” để tìm đỉnh thật.")

# ----------------------------------------------------------------- KPIs
if best:
    with st.container(border=True):
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Vốn nạp tối ưu Δx*", fmt(best.optimal_amount_in, base_token))
        k2.metric("Sản lượng kỳ vọng", fmt(best.expected_output, base_token))
        roi = best.max_net_profit / best.optimal_amount_in * 100 if best.optimal_amount_in else 0.0
        k3.metric("Lãi ròng cực đại", fmt(best.max_net_profit, base_token), f"{roi:+.2f}% trên vốn",
                  help="Đã trừ phí swap, trượt giá và gas.")
        k4.metric("Slippage guard (minTotalReturn)",
                  fmt(best.min_total_return, base_token) if best.verdict == "EXECUTE" else "—",
                  help="Số dư tối thiểu để hợp đồng on-chain không revert (dung sai 0.2%).")


# ----------------------------------------------------------------- figures
def network_figure(G, cycle):
    known = {"WETH": (0, 1.15), "USDC": (-1.25, 0), "USDT": (1.25, 0), "DAI": (0, -1.15)}
    if set(G.nodes) <= set(known):
        pos = {n: np.array(known[n], float) for n in G.nodes}
    else:
        pos = {n: np.array(p) * 1.2 for n, p in nx.circular_layout(G).items()}
    cyc_edges = set(zip(cycle, cycle[1:])) if cycle else set()
    cyc_pairs = {frozenset(e) for e in cyc_edges}

    fig = go.Figure()
    seen = set()
    for u, v, d in G.edges(data=True):
        pair = frozenset((u, v))
        if pair in seen:
            continue
        seen.add(pair)

        # 1. TRUY XUẤT SPOT AN TOÀN CHO CHIỀU ĐI (u -> v)
        spot_fwd = d.get('spot')
        if spot_fwd is None:
            r_in, r_out, fee = d.get('r_in', 0), d.get('r_out', 0), d.get('fee', 0.003)
            spot_fwd = (r_out / r_in * (1.0 - fee)) if r_in > 0 else 1.0

        # 2. TRUY XUẤT SPOT AN TOÀN CHO CHIỀU VỀ (v -> u) NẾU CÓ
        spot_back_val = 1.0
        if G.has_edge(v, u):
            back_data = G[v][u]
            # Nếu MultiDiGraph trả về dict chứa các edge keys
            if isinstance(back_data, dict) and not any(k in back_data for k in ['spot', 'r_in', 'fee']):
                first_k = next(iter(back_data))
                back_data = back_data[first_k]

            spot_back_val = back_data.get('spot')
            if spot_back_val is None:
                r_in_b, r_out_b, fee_b = back_data.get('r_in', 0), back_data.get('r_out', 0), back_data.get('fee', 0.003)
                spot_back_val = (r_out_b / r_in_b * (1.0 - fee_b)) if r_in_b > 0 else 1.0

        p0, p1 = pos[u], pos[v]
        on = pair in cyc_pairs
        fig.add_trace(go.Scatter(x=[p0[0], p1[0]], y=[p0[1], p1[1]], mode="lines", hoverinfo="skip",
                                 line=dict(color=RED if on else "rgba(148,163,184,0.45)", width=2 if on else 1.5),
                                 showlegend=False))
        mid = (p0 + p1) / 2

        pool_name = d.get('pool_name', d.get('pool', 'Pool'))
        fig.add_trace(go.Scatter(
            x=[mid[0]], y=[mid[1]], mode="markers", marker=dict(size=14, color="rgba(0,0,0,0)"),
            showlegend=False, hoverinfo="text",
            text=(f"<b>{pool_name}</b><br>{u}→{v}: {spot_fwd:.6g}<br>{v}→{u}: {spot_back_val:.6g}"
                  f"<br>Reserve: {d.get('r_in', 0):,.2f} {u} / {d.get('r_out', 0):,.2f} {v}")))

    for u, v in cyc_edges:
        fig.add_annotation(x=pos[v][0], y=pos[v][1], ax=pos[u][0], ay=pos[u][1], xref="x", yref="y",
                           axref="x", ayref="y", showarrow=True, arrowhead=3, arrowsize=1.4,
                           arrowwidth=3.5, arrowcolor=RED, standoff=20, startstandoff=20)
    names = list(G.nodes)
    fig.add_trace(go.Scatter(
        x=[pos[n][0] for n in names], y=[pos[n][1] for n in names], mode="markers+text", text=names,
        textposition="top center", textfont=dict(size=13), hoverinfo="skip", showlegend=False,
        marker=dict(size=26, color=[GREEN if n == base_token else BLUE for n in names],
                    line=dict(width=2, color="white"))))
    fig.update_layout(height=430, margin=dict(l=5, r=5, t=5, b=5), plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False, range=[-1.8, 1.8]), yaxis=dict(visible=False, range=[-1.55, 1.55]))
    return fig


def profit_figure(o, base):
    x, y = o.capital_range, o.profits
    roots = [x[i] - y[i] * (x[i + 1] - x[i]) / (y[i + 1] - y[i])
             for i in range(len(x) - 1) if y[i] * y[i + 1] < 0]
    fig = go.Figure()
    if y.max() > 0:
        lo = roots[0] if roots and y[0] <= 0 else x[0]
        hi = roots[-1] if roots and y[-1] <= 0 else x[-1]
        fig.add_vrect(x0=lo, x1=hi, fillcolor="rgba(16,185,129,0.10)", line_width=0,
                      annotation_text="Vùng có lãi", annotation_position="top left",
                      annotation_font=dict(color=GREEN, size=11))
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="Π(Δx)", line=dict(color=GREEN if y.max() > 0 else RED, width=3),
                             hovertemplate="Vốn: %{x:.3f}<br>Lãi ròng: %{y:.4f}<extra></extra>"))
    fig.add_hline(y=0, line_dash="dash", line_color=GRAY, line_width=1)
    fig.add_trace(go.Scatter(x=[o.optimal_amount_in], y=[o.max_net_profit], mode="markers+text", name="Δx*",
                             text=[f"Δx* = {o.optimal_amount_in:.3f}"], textposition="top center",
                             marker=dict(color=AMBER, size=11, line=dict(width=2, color="white"))))
    fig.update_layout(
        height=430, margin=dict(l=5, r=5, t=30, b=5), plot_bgcolor="rgba(0,0,0,0)", showlegend=False,
        xaxis=dict(title=f"Vốn nạp Δx ({base})", gridcolor=GRID),
        yaxis=dict(title=f"Lãi ròng ({base})", gridcolor=GRID, zerolinecolor=GRID))
    return fig, roots


col_g, col_c = st.columns([1, 1.25], gap="large")
with col_g:
    st.subheader("Mạng lưới thanh khoản")
    show(network_figure(engine.graph, best.cycle if best else None), key="net")
    st.caption("Chấm xanh lá: token gốc · Mũi tên đỏ: chiều chu trình · Rê chuột vào giữa cạnh để xem pool.")

with col_c:
    st.subheader("Hàm lợi nhuận ròng Π(Δx)")
    if best:
        fig, roots = profit_figure(best, base_token)
        show(fig, key="curve")
        if roots:
            st.caption(f"Hòa vốn tại Δx ≈ {', '.join(f'{r:,.3f}' for r in roots)} {base_token}. "
                       f"Ngoài vùng này, trượt giá và gas làm giao dịch lỗ.")
        else:
            st.caption("Trong dải vốn đang quét, hàm lợi nhuận không cắt trục hòa vốn.")
    else:
        st.info("Chưa có chu trình hợp lệ để tối ưu vốn.")

# ----------------------------------------------------------------- detail tabs
tab_hops, tab_cands, tab_payload, tab_pools = st.tabs(
    ["Chi tiết từng hop", "Chu trình ứng viên", "Payload on-chain", "Dữ liệu pool"])

with tab_hops:
    if best:
        rows = [{"Hop": i + 1, "Pool": h.pool_name, "Hoán đổi": f"{h.token_in} → {h.token_out}",
                 "Số vào": round(h.amount_in, 4), "Số ra": round(h.amount_out, 4),
                 "Tỷ giá spot": round(h.spot_rate, 6), "Tỷ giá thực": round(h.effective_rate, 6),
                 "Trượt giá (%)": round(h.price_impact * 100, 3)} for i, h in enumerate(best.hops)]
        table(rows)
        st.caption(f"Tích tỷ giá spot quanh chu trình: ×{best.spot_return:.4f} "
                   f"(lợi thế lý thuyết khi Δx → 0, trước trượt giá và gas).")
    else:
        st.write("Không có dữ liệu.")

with tab_cands:
    if opps:
        table([{"Chu trình": " → ".join(o.cycle), "Số hop": len(o.cycle) - 1,
                "Spot ×": round(o.spot_return, 4), "Δx*": round(o.optimal_amount_in, 4),
                f"Lãi ròng ({base_token})": round(o.max_net_profit, 4), "Quyết định": o.verdict}
               for o in opps])
        st.caption("Mọi chu trình đi qua token gốc có Σw < 0, xếp theo lãi ròng. Dòng đầu được chọn.")
    else:
        st.write("Không có chu trình ứng viên.")

# ============================================================ EXECUTE TAB
with tab_payload:
    if not (best and best.verdict == "EXECUTE"):
        st.write("Payload chỉ được tạo khi quyết định là EXECUTE (NFR-03).")
    else:
        st.markdown(
            f"**Chu trình:** `{' → '.join(best.cycle)}` · "
            f"**Δx\\***: `{best.optimal_amount_in:.6f} {base_token}` · "
            f"**Lãi ròng kỳ vọng:** `{best.max_net_profit:.6f} {base_token}`"
        )

        # --- Cấu hình kết nối ---
        with st.container(border=True):
            st.markdown("##### Kết nối ví (chỉ dùng testnet!)")
            ex1, ex2 = st.columns([3, 2])
            exec_rpc = ex1.text_input(
                "RPC URL (Sepolia)",
                value=rpc_url if not mock and rpc_url else "",
                placeholder="https://eth-sepolia.g.alchemy.com/v2/…",
                key="exec_rpc",
                help="Có thể dùng cùng RPC ở thanh bên, hoặc một RPC khác.",
            )
            exec_pk = ex2.text_input(
                "Private key", type="password", key="exec_pk",
                help="KHÔNG BAO GIỜ dùng ví mainnet ở đây. Chỉ ví Sepolia chứa ETH test.",
            )

            p1, p2 = st.columns(2)
            slippage_pct = p1.slider(
                "Slippage tối đa (%)", 0.1, 5.0, 0.5, 0.1, key="exec_slip",
                help="amountOutMin = quote × (1 - slippage). Vượt ngưỡng → tx revert.",
            )
            dry_click = p1.button("🔍 Dry-run (quote only)", use_container_width=True)
            live_click = p2.button(
                "🚀 Execute on-chain",
                type="primary",
                use_container_width=True,
                disabled=not (exec_rpc and exec_pk),
                help="Gửi swapExactTokensForTokens thật lên Sepolia. Tiêu gas.",
            )

        # --- Thực thi ---
        if dry_click or live_click:
            try:
                from executor import SwapExecutor  # import lazy: chỉ cần khi bấm
                ex = SwapExecutor(exec_rpc, exec_pk)
                st.caption(f"Ví thực thi: `{ex.address}` · "
                           f"Số dư {base_token}: `{ex.balance(base_token):.6f}`")
                with st.spinner("Đang xử lý giao dịch…"):
                    res = ex.execute_opportunity(
                        best,
                        slippage=slippage_pct / 100.0,
                        dry_run=dry_click,
                    )

                if dry_click:
                    if res.ok:
                        st.info(
                            "Dry-run thành công. Quote on-chain (wei):\n\n"
                            + " → ".join(f"`{a:,}`" for a in res.amounts)
                            + f"\n\n≈ **{res.amounts[-1] / 10 ** TOKENS[best.cycle[-1]][1]:.6f} "
                              f"{best.cycle[-1]}**"
                        )
                    else:
                        st.error(f"Dry-run thất bại: {res.error}")
                else:
                    if res.ok:
                        st.success(f"✅ Giao dịch thành công · block {res.block} · "
                                   f"gas {res.gas_used:,}")
                        st.markdown(
                            f"[Xem trên Sepolia Etherscan]"
                            f"(https://sepolia.etherscan.io/tx/{res.tx_hash})"
                        )
                        st.json({
                            "tx_hash": res.tx_hash,
                            "block": res.block,
                            "gas_used": res.gas_used,
                            "amounts_wei": res.amounts,
                            "amounts_human": [
                                f"{a / 10 ** TOKENS[t][1]:.6f} {t}"
                                for a, t in zip(res.amounts, best.cycle)
                            ],
                        })
                    else:
                        st.error(f"❌ {res.error}")
                        if res.tx_hash:
                            st.markdown(
                                f"[Xem tx lỗi trên Etherscan]"
                                f"(https://sepolia.etherscan.io/tx/{res.tx_hash})"
                            )
            except ImportError:
                st.error("Không import được `executor.py`. Hãy tạo file này cùng thư mục với `app.py`.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Lỗi executor: {exc}")

        # --- Payload tham chiếu (giữ nguyên) ---
        with st.expander("Payload on-chain (raw JSON)"):
            dec = TOKENS[base_token][1]
            st.json({
                "function": "executeArbitrage",
                "hops": [{"pool": h.pool_address,
                          "tokenIn": TOKENS[h.token_in][0],
                          "tokenOut": TOKENS[h.token_out][0],
                          "poolType": 0} for h in best.hops],
                "amountIn": str(int(round(best.optimal_amount_in * 10 ** dec))),
                "minTotalReturn": str(int(best.min_total_return * 10 ** dec)),
            })
            st.caption(
                "Khớp giao diện IArbitrageExecutor.executeArbitrage (dùng khi có smart contract riêng). "
                "Nút Execute ở trên hiện gọi trực tiếp UniswapV2Router02."
            )

with tab_pools:
    table([{"Pool": p["pool_name"], "Cặp": f"{p['token0']}/{p['token1']}",
            "Reserve 0": round(p["reserve0"], 2), "Reserve 1": round(p["reserve1"], 2),
            "Phí (%)": p.get("fee", 0.003) * 100, "Địa chỉ": p["pool_address"]} for p in pools])