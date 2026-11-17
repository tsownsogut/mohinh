"""Phân hệ thu thập dữ liệu on-chain (FR-01).

Hai chế độ:
  - Mock : dữ liệu mô phỏng, điều khiển bằng slider (stress test).
  - RPC  : đọc getReserves() thật từ các pool Uniswap V2 trên **Sepolia testnet**
           (hoặc mainnet) qua Web3 eth_call — không tốn gas.

Địa chỉ pool được tra cứu ĐỘNG qua Factory.getPair(tokenA, tokenB), nên chỉ cần
khai báo cặp token muốn theo dõi trong DEFAULT_PAIRS — pool nào chưa tồn tại trên
testnet sẽ được bỏ qua một cách an toàn.
"""
from __future__ import annotations

# ============================================================================
# REGISTRY: SEPOLIA TESTNET (mặc định)
# ============================================================================
TOKENS = {
    "WETH": ("0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14", 18),
    "USDC": ("0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", 6),
    "USDT": ("0xaA8E23Fb1079EA71e0a56F48a2aA51851D8433D0", 6),
    "DAI":  ("0xFF34B3d4Aee8ddCd6F9AFFFB6Fe49bD371b8a357", 18),
}

# ----------------------------------------------------------------------------
# Hạ tầng Uniswap V2 trên Sepolia (dùng lại được cho executor.py)
# ----------------------------------------------------------------------------
SEPOLIA_FACTORY = "0xF62c03E08ada871A0bEb309762E260a7a6a880E6"
SEPOLIA_ROUTER  = "0xeE567Fe1712Faf6149d80dA1E6934E354124CfE3"
SEPOLIA_WETH    = "0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14"

# Cặp token cần theo dõi: (tên hiển thị, tokenA, tokenB)
# Địa chỉ pool sẽ được resolve động — không hardcode.
DEFAULT_PAIRS: list[tuple[str, str, str]] = [
    ("UniV2_WETH_USDC", "WETH", "USDC"),
    ("UniV2_WETH_USDT", "WETH", "USDT"),
    ("UniV2_DAI_WETH",  "DAI",  "WETH"),
    ("UniV2_DAI_USDC",  "DAI",  "USDC"),
    ("UniV2_DAI_USDT",  "DAI",  "USDT"),
]

# ----------------------------------------------------------------------------
# ABI tối thiểu
# ----------------------------------------------------------------------------
_PAIR_ABI = [
    {"name": "getReserves", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "r0", "type": "uint112"}, {"name": "r1", "type": "uint112"},
                 {"name": "ts", "type": "uint32"}]},
    {"name": "token0", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "address"}]},
    {"name": "token1", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "address"}]},
]

_FACTORY_ABI = [
    {"name": "getPair", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenA", "type": "address"},
                {"name": "tokenB", "type": "address"}],
     "outputs": [{"name": "pair", "type": "address"}]},
]

_ZERO_ADDR = "0x0000000000000000000000000000000000000000"


class OnChainReader:
    def __init__(self, rpc_url: str | None = None):
        self.rpc_url = rpc_url
        self.w3 = None

    # ------------------------------------------------------------------ RPC
    def connect(self):
        from web3 import Web3  # import trễ: chế độ Mock không cần web3
        if not self.rpc_url:
            raise ValueError("Chưa cấu hình RPC URL.")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 10}))
        if not self.w3.is_connected():
            raise ConnectionError(f"Không kết nối được RPC node: {self.rpc_url}")
        return self

    # --------------------------------------------------------- Pool lookup
    def get_pair_address(
        self,
        token_a: str,
        token_b: str,
        factory_addr: str = SEPOLIA_FACTORY,
    ) -> str:
        """Tra cứu địa chỉ Pair qua Factory.getPair(). Trả về ZERO_ADDR nếu chưa tồn tại."""
        if self.w3 is None:
            self.connect()
        factory = self.w3.eth.contract(
            address=self.w3.to_checksum_address(factory_addr), abi=_FACTORY_ABI
        )
        return factory.functions.getPair(
            self.w3.to_checksum_address(TOKENS[token_a][0]),
            self.w3.to_checksum_address(TOKENS[token_b][0]),
        ).call()

    def _read_pair(self, name: str, pair_addr: str) -> dict | None:
        """Đọc reserve của một Pair. Trả về None nếu chưa có pool / reserve = 0."""
        c = self.w3.eth.contract(
            address=self.w3.to_checksum_address(pair_addr), abi=_PAIR_ABI
        )
        try:
            r0, r1, _ = c.functions.getReserves().call()
        except Exception:
            return None
        if r0 == 0 or r1 == 0:
            return None  # pool rỗng — bỏ qua

        by_addr = {addr.lower(): (sym, dec) for sym, (addr, dec) in TOKENS.items()}
        t0 = c.functions.token0().call().lower()
        t1 = c.functions.token1().call().lower()
        if t0 not in by_addr or t1 not in by_addr:
            return None  # token lạ — bỏ qua
        s0, d0 = by_addr[t0]
        s1, d1 = by_addr[t1]

        return {
            "pool_name": name,
            "pool_address": pair_addr,
            "token0": s0, "token1": s1,
            "reserve0": r0 / 10 ** d0,
            "reserve1": r1 / 10 ** d1,
            "fee": 0.003,
        }

    def get_live_pools(
        self,
        pairs: list[tuple[str, str, str]] | None = None,
        factory_addr: str = SEPOLIA_FACTORY,
        warn_empty: bool = True,
    ) -> list[dict]:
        """Đọc reserve thật cho mọi cặp trong `pairs`. Bỏ qua pool chưa tồn tại."""
        if self.w3 is None:
            self.connect()
        pairs = pairs or DEFAULT_PAIRS
        out, skipped = [], []
        for name, ta, tb in pairs:
            pair_addr = self.get_pair_address(ta, tb, factory_addr)
            if pair_addr == _ZERO_ADDR:
                skipped.append(f"{name} (chưa tạo pool)")
                continue
            info = self._read_pair(name, pair_addr)
            if info is None:
                skipped.append(f"{name} ({pair_addr[:10]}… reserve=0)")
            else:
                out.append(info)

        if warn_empty and not out:
            raise RuntimeError(
                "Không đọc được pool nào có thanh khoản. "
                "Hãy tạo pool trên Sepolia trước — xem scripts/setup-pools.js. "
                f"Bỏ qua: {', '.join(skipped) if skipped else 'không có cặp nào'}"
            )
        if skipped and warn_empty:
            # Không raise, chỉ in ra để dev biết
            print(f"[reader] Bỏ qua {len(skipped)} pool: {', '.join(skipped)}")
        return out

    # ----------------------------------------------------------------- Mock
    @staticmethod
    def get_mock_pools(dai_price: float = 2400.0, liquidity_factor: float = 1.0,
                       active_stable: str = "USDC") -> list[dict]:
        """
        dai_price        : số DAI đổi được cho 1 WETH trong pool DAI/WETH (chuẩn thị trường ~2500).
        liquidity_factor : hệ số nhân độ sâu thanh khoản của mọi pool.
        active_stable    : 'USDC' hoặc 'USDT' chọn nhánh stablecoin ưu thế tỷ giá.
        """
        k = liquidity_factor
        rate_usdc_bonus = 1.025 if active_stable == "USDC" else 1.0
        rate_usdt_bonus = 1.025 if active_stable == "USDT" else 1.0

        return [
            {"pool_name": "UniV2_WETH_USDC", "pool_address": "0xPoolWethUsdc",
             "token0": "WETH", "token1": "USDC",
             "reserve0": 600.0 * k, "reserve1": 1_500_000.0 * k * rate_usdc_bonus, "fee": 0.003},
            {"pool_name": "UniV2_WETH_USDT", "pool_address": "0xPoolWethUsdt",
             "token0": "WETH", "token1": "USDT",
             "reserve0": 600.0 * k, "reserve1": 1_500_000.0 * k * rate_usdt_bonus, "fee": 0.003},
            {"pool_name": "Sushi_USDC_DAI", "pool_address": "0xPoolUsdcDai",
             "token0": "USDC", "token1": "DAI",
             "reserve0": 3_000_000.0 * k, "reserve1": 3_000_000.0 * k, "fee": 0.003},
            {"pool_name": "Sushi_USDT_DAI", "pool_address": "0xPoolUsdtDai",
             "token0": "USDT", "token1": "DAI",
             "reserve0": 3_000_000.0 * k, "reserve1": 3_000_000.0 * k, "fee": 0.003},
            {"pool_name": "UniV2_DAI_WETH", "pool_address": "0xPoolDaiWeth",
             "token0": "DAI", "token1": "WETH",
             "reserve0": dai_price * 600.0 * k, "reserve1": 600.0 * k, "fee": 0.003},
        ]