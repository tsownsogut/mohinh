"""Phân hệ thực thi: ký & gửi giao dịch swap lên Uniswap V2 (Sepolia).

Hai API chính:
    executor.swap_token_to_token(...)   # Task 3.1 — path = [A, B]
    executor.swap_multi_hop(...)        # Task 3.2 — path = [A, B, C, D]

Cả hai đều gọi swapExactTokensForTokens trên Router02, nên chỉ khác độ dài path.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted

from reader import TOKENS, SEPOLIA_ROUTER, SEPOLIA_WETH

# ---------------------------------------------------------------------------
# ABI tối thiểu cho Router02 / ERC20
# ---------------------------------------------------------------------------
_ROUTER_ABI = [
    {
        "name": "swapExactTokensForTokens",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
]

_ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint8"}]},
]


@dataclass
class TxResult:
    ok: bool
    tx_hash: str
    block: int | None
    gas_used: int | None
    amounts: list[int]          # [amountIn, out_hop1, out_hop2, ...] theo đơn vị wei
    error: str | None = None


class SwapExecutor:
    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        router_addr: str = SEPOLIA_ROUTER,
        gas_price_gwei: float | None = None,   # None = lấy từ mạng
        deadline_seconds: int = 60,
    ):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
        if not self.w3.is_connected():
            raise ConnectionError(f"Không kết nối được RPC: {rpc_url}")

        self.acct = self.w3.eth.account.from_key(private_key)
        self.address = self.acct.address
        self.router_addr = self.w3.to_checksum_address(router_addr)
        self.router = self.w3.eth.contract(address=self.router_addr, abi=_ROUTER_ABI)
        self.gas_price = self.w3.to_wei(gas_price_gwei, "gwei") if gas_price_gwei else None
        self.deadline_seconds = deadline_seconds

    # ------------------------------------------------------------ helpers
    def _erc20(self, symbol: str):
        addr = self.w3.to_checksum_address(TOKENS[symbol][0])
        return self.w3.eth.contract(address=addr, abi=_ERC20_ABI)

    def _to_wei(self, symbol: str, amount: float) -> int:
        return int(round(amount * 10 ** TOKENS[symbol][1]))

    def _from_wei(self, symbol: str, amount: int) -> float:
        return amount / 10 ** TOKENS[symbol][1]

    def balance(self, symbol: str) -> float:
        return self._from_wei(symbol, self._erc20(symbol).functions.balanceOf(self.address).call())

    def get_amounts_out(self, path: list[str], amount_in_wei: int) -> list[int]:
        """Mô phỏng quote on-chain (view call) — dùng để tính amountOutMin."""
        path_addr = [self.w3.to_checksum_address(TOKENS[s][0]) for s in path]
        return self.router.functions.getAmountsOut(amount_in_wei, path_addr).call()

    def ensure_allowance(self, symbol: str, amount_wei: int) -> str | None:
        """Approve Router nếu allowance < amount. Trả về tx_hash nếu có approve, None nếu đã đủ."""
        token = self._erc20(symbol)
        current = token.functions.allowance(self.address, self.router_addr).call()
        if current >= amount_wei:
            return None

        tx = token.functions.approve(self.router_addr, 2**256 - 1).build_transaction(
            self._base_tx()
        )
        signed = self.acct.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.w3.eth.wait_for_transaction_receipt(h, timeout=120)
        return h.hex()

    def _base_tx(self) -> dict:
        nonce = self.w3.eth.get_transaction_count(self.address, "pending")
        tx = {
            "from": self.address,
            "nonce": nonce,
            "chainId": self.w3.eth.chain_id,
        }
        if self.gas_price:
            tx["gasPrice"] = self.gas_price
        else:
            # EIP-1559 trên Sepolia
            latest = self.w3.eth.get_block("latest")
            base = latest.get("baseFeePerGas", self.w3.to_wei(1, "gwei"))
            tx["maxFeePerGas"] = base * 2 + self.w3.to_wei(1, "gwei")
            tx["maxPriorityFeePerGas"] = self.w3.to_wei(1, "gwei")
        return tx

    # ------------------------------------------------------------ execute
    def _execute_path(
        self,
        path: list[str],
        amount_in_wei: int,
        slippage: float = 0.005,
        dry_run: bool = False,
    ) -> TxResult:
        """Gửi swapExactTokensForTokens. Nếu dry_run=True chỉ quote, không gửi."""
        if len(path) < 2:
            return TxResult(False, "", None, None, [], "path phải có ít nhất 2 token")

        # 1. Quote on-chain
        try:
            amounts = self.get_amounts_out(path, amount_in_wei)
        except ContractLogicError as exc:
            return TxResult(False, "", None, None, [], f"Quote lỗi: {exc}")

        expected_out = amounts[-1]
        amount_out_min = int(expected_out * (1 - slippage))

        if dry_run:
            print(f"[dry-run] {path} | in={amount_in_wei} | out≈{expected_out} | "
                  f"min={amount_out_min}")
            return TxResult(True, "DRY_RUN", None, None, amounts)

        # 2. Approve token đầu vào
        try:
            approve_hash = self.ensure_allowance(path[0], amount_in_wei)
            if approve_hash:
                print(f"[approve] {path[0]} → Router | tx={approve_hash}")
        except Exception as exc:
            return TxResult(False, "", None, None, [], f"Approve lỗi: {exc}")

        # 3. Build + sign + send
        path_addr = [self.w3.to_checksum_address(TOKENS[s][0]) for s in path]
        deadline = int(time.time()) + self.deadline_seconds
        try:
            fn = self.router.functions.swapExactTokensForTokens(
                amount_in_wei, amount_out_min, path_addr, self.address, deadline
            )
            tx = fn.build_transaction(self._base_tx())
            tx["gas"] = int(self.w3.eth.estimate_gas(tx) * 1.2)
            signed = self.acct.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction).hex()
            print(f"[send] tx={tx_hash}")
        except ContractLogicError as exc:
            return TxResult(False, "", None, None, [], f"Revert khi build: {exc}")
        except Exception as exc:
            return TxResult(False, "", None, None, [], f"Gửi lỗi: {exc}")

        # 4. Chờ receipt
        try:
            rcpt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        except TimeExhausted:
            return TxResult(False, tx_hash, None, None, [], "Timeout chờ receipt")

        ok = rcpt.status == 1
        return TxResult(
            ok=ok,
            tx_hash=tx_hash,
            block=rcpt.blockNumber,
            gas_used=rcpt.gasUsed,
            amounts=amounts,
            error=None if ok else "Revert on-chain",
        )

    # ------------------------------------------------------------ public API
    def swap_token_to_token(
        self,
        token_in: str,
        token_out: str,
        amount_in: float,
        slippage: float = 0.005,
        dry_run: bool = False,
    ) -> TxResult:
        """Task 3.1 — Swap trực tiếp một cặp (path = [A, B])."""
        return self._execute_path(
            [token_in, token_out], self._to_wei(token_in, amount_in), slippage, dry_run
        )

    def swap_multi_hop(
        self,
        path: list[str],
        amount_in: float,
        slippage: float = 0.005,
        dry_run: bool = False,
    ) -> TxResult:
        """Task 3.2 — Swap đa hop (path = [A, B, C, D]). AMM tự sử dụng pool từng cặp."""
        return self._execute_path(
            path, self._to_wei(path[0], amount_in), slippage, dry_run
        )

    def execute_opportunity(self, best, slippage: float = 0.005, dry_run: bool = False) -> TxResult:
        """Nhận trực tiếp Opportunity từ engine.opportunize_capital và thực thi."""
        if best is None:
            return TxResult(False, "", None, None, [], "Opportunity rỗng")
        if best.verdict != "EXECUTE":
            return TxResult(False, "", None, None, [], f"Verdict={best.verdict} — không gửi")
        return self._execute_path(
            best.cycle, self._to_wei(best.cycle[0], best.optimal_amount_in),
            slippage, dry_run,
        )