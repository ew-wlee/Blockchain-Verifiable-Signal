import "dotenv/config";
import { HardhatUserConfig } from "hardhat/config";
import "@nomicfoundation/hardhat-toolbox";

const DEFAULT_SEPOLIA_RPC = "https://ethereum-sepolia-rpc.publicnode.com";
const privateKey = process.env.SEPOLIA_PRIVATE_KEY?.trim();
const accounts = privateKey ? [privateKey] : [];

const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  paths: {
    sources: "./contracts",
    tests: "./test",
    cache: "./build/hardhat-cache",
    artifacts: "./build/hardhat-artifacts",
  },
  networks: {
    hardhat: {
      chainId: 31337,
    },
    sepolia: {
      url: process.env.SEPOLIA_RPC_URL?.trim() || DEFAULT_SEPOLIA_RPC,
      chainId: 11155111,
      accounts,
    },
  },
};

export default config;
