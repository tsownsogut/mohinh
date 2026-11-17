import { network } from "hardhat";

const ROUTER = "0xeE567Fe1712Faf6149d80dA1E6934E354124CfE3"; // Uniswap V2 Router Sepolia
const FACTORY = "0xF62c03E08ada871A0bEb309762E260a7a6a880E6"; // Uniswap V2 Factory Sepolia

async function main() {
  // Khởi tạo kết nối mạng và lấy thư viện ethers (Cú pháp của Hardhat v3)
  const { ethers } = await network.create();
  const [signer] = await ethers.getSigners();
  
  console.log("Đang dùng ví:", signer.address);

  const Token = await ethers.getContractFactory("TestERC20");
  
  // 1. Deploy 3 token test (TKA, TKB, TKC)
  console.log("Đang deploy 3 token test...");
  const tka = await Token.deploy("Test Token A", "TKA", ethers.parseEther("1000000"));
  const tkb = await Token.deploy("Test Token B", "TKB", ethers.parseEther("1000000"));
  const tkc = await Token.deploy("Test Token C", "TKC", ethers.parseEther("1000000"));
  
  await tka.waitForDeployment();
  await tkb.waitForDeployment();
  await tkc.waitForDeployment();

  const addrA = await tka.getAddress();
  const addrB = await tkb.getAddress();
  const addrC = await tkc.getAddress();

  console.log("Token A (TKA):", addrA);
  console.log("Token B (TKB):", addrB);
  console.log("Token C (TKC):", addrC);

  // 2. Approve Router
  const router = await ethers.getContractAt([
    "function addLiquidity(address,address,uint256,uint256,uint256,uint256,address,uint256) returns (uint256,uint256,uint256)"
  ], ROUTER, signer);

  console.log("Đang approve Router cho 3 token...");
  await (await tka.approve(ROUTER, ethers.MaxUint256)).wait();
  await (await tkb.approve(ROUTER, ethers.MaxUint256)).wait();
  await (await tkc.approve(ROUTER, ethers.MaxUint256)).wait();
  console.log("Approve thành công!");

  // 3. Tạo 3 Pool với tỷ giá LỆCH để tạo Arbitrage
  const deadline = Math.floor(Date.now() / 1000) + 3600;

  console.log("Đang tạo Pool 1 (TKA/TKB)...");
  let tx = await router.addLiquidity(addrA, addrB, ethers.parseEther("1000"), ethers.parseEther("100000"), 0, 0, signer.address, deadline);
  await tx.wait();

  console.log("Đang tạo Pool 2 (TKB/TKC)...");
  tx = await router.addLiquidity(addrB, addrC, ethers.parseEther("50000"), ethers.parseEther("100000"), 0, 0, signer.address, deadline);
  await tx.wait();

  console.log("Đang tạo Pool 3 (TKC/TKA)...");
  tx = await router.addLiquidity(addrC, addrA, ethers.parseEther("50000"), ethers.parseEther("27500"), 0, 0, signer.address, deadline);
  await tx.wait();

  // 4. Lấy địa chỉ các Pool vừa tạo
  const factory = await ethers.getContractAt(["function getPair(address,address) view returns (address)"], FACTORY, signer);
  const pairAB = await factory.getPair(addrA, addrB);
  const pairBC = await factory.getPair(addrB, addrC);
  const pairCA = await factory.getPair(addrC, addrA);

  console.log("\n=== COPY ĐOẠN NÀY VÀO reader.py ===");
  console.log(`TOKENS = {
    "TKA": ("${addrA}", 18),
    "TKB": ("${addrB}", 18),
    "TKC": ("${addrC}", 18),
}

DEFAULT_PAIRS = [
    ("Test_TKA_TKB", "TKA", "TKB"),
    ("Test_TKB_TKC", "TKB", "TKC"),
    ("Test_TKC_TKA", "TKC", "TKA"),
]`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});