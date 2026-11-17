import { defineConfig } from "hardhat/config";
import hardhatEthers from "@nomicfoundation/hardhat-ethers";

export default defineConfig({
  solidity: "0.8.20",
  plugins: [hardhatEthers],
  networks: {
    sepolia: {
      type: "http",
      url: "https://eth-mainnet.g.alchemy.com/v2/alch_qGlG7VNsUX_SN2guRvt4z", 
      accounts: ["ab9f76732a34cb6c71db7cb95801a7ea0aca18d4d4e3297404ec8e9fe250a9f2"], // Nhớ điền Private Key
    },
  },
});