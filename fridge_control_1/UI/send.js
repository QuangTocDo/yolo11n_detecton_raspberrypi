// Import các thư viện cần thiết
import { ethers } from "ethers";
import { readFile } from "fs/promises";
import "dotenv/config";

// Hàm để thiết lập nhiệt độ mục tiêu
export async function setTargetTemperature(temperature) {
  const contract = await setupContract();
  const tempValue = ethers.parseUnits(temperature, 2);
  const tx = await contract.setTargetTemperature(tempValue);
  await tx.wait();
  console.log(`✅ Đã thiết lập nhiệt độ mục tiêu thành: ${temperature}°C`);
}

// Hàm để thiết lập độ ẩm mục tiêu
export async function setTargetHumidity(humidity) {
  const contract = await setupContract();
  const humidityValue = ethers.parseUnits(humidity, 2);
  const tx = await contract.setTargetHumidity(humidityValue);
  await tx.wait();
  console.log(`✅ Đã thiết lập độ ẩm mục tiêu thành: ${humidity}%`);
}

// Hàm helper để cài đặt và trả về contract instance
async function setupContract() {
  // SỬA ĐỔI: Đọc RPC_URL_WS từ file .env
  const rpcUrl = process.env.RPC_URL_WS;
  const contractAddress = process.env.CONTRACT_ADDRESS;
  const privateKey = process.env.PRIVATE_KEY;

  if (!rpcUrl || !contractAddress || !privateKey) {
    throw new Error(
      "Vui lòng điền đầy đủ RPC_URL_WS, CONTRACT_ADDRESS, và PRIVATE_KEY trong file .env"
    );
  }

  // SỬA ĐỔI: Sử dụng WebSocketProvider thay vì JsonRpcProvider
  const provider = new ethers.WebSocketProvider(rpcUrl);
  const wallet = new ethers.Wallet(privateKey, provider);
  const abi = JSON.parse(
    await readFile(new URL("./abi.json", import.meta.url))
  );

  return new ethers.Contract(contractAddress, abi, wallet);
}
