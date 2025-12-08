// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract SmartFridgeController {

    int256 public targetTemperature;
    uint256 public targetHumidity;

    event TargetTemperatureSet(int256 newTargetTemperature);
    event TargetHumiditySet(uint256 newTargetHumidity);

    function setTargetTemperature(int256 _newTargetTemperature) public {
        targetTemperature = _newTargetTemperature;
        emit TargetTemperatureSet(_newTargetTemperature);
    }

    function setTargetHumidity(uint256 _newTargetHumidity) public {
        targetHumidity = _newTargetHumidity;
        emit TargetHumiditySet(_newTargetHumidity);
    }

    struct SensorReading {
        int256 temperature;
        uint256 humidity;
        uint256 power;
        uint256 timestamp;
    }

    int256 public latestSensorTemperature;
    uint256 public latestSensorHumidity;
    uint256 public latestSensorPower;
    string public lastSystemError;
    uint256 public latestTotalEnergyWhScaled;

    SensorReading[] public history;

    event SensorDataUpdated(int256 newTemperature, uint256 newHumidity, uint256 power, uint256 timestamp);
    event SystemErrorOccurred(string reason, uint256 timestamp);
     event EnergyReported(uint256 totalEnergyWhScaled, uint256 timestamp);

    function updateSensorData(int256 _currentTemperature, uint256 _currentHumidity, uint256 _currentPower) public {
        // 1. Cập nhật các biến lưu giá trị mới nhất
        latestSensorTemperature = _currentTemperature;
        latestSensorHumidity = _currentHumidity;
        latestSensorPower = _currentPower;

        // 2. Lấy thời gian hiện tại của blockchain
        uint256 currentTime = block.timestamp;

        // 3. Thêm bản ghi mới vào lịch sử
        history.push(SensorReading({
            temperature: _currentTemperature,
            humidity: _currentHumidity,
            power: _currentPower,
            timestamp: currentTime
        }));

        // 4. Phát ra sự kiện để Frontend có thể lắng nghe
        emit SensorDataUpdated(_currentTemperature, _currentHumidity, _currentPower, currentTime);
    }

    function reportError(string memory _reason) public {
       uint256 currentTime = block.timestamp;
       lastSystemError = _reason;
       emit SystemErrorOccurred(_reason, currentTime);
    }

    function reportEnergyUsage(uint256 _totalEnergyWhScaled) public {
        uint256 currentTime = block.timestamp;
        latestTotalEnergyWhScaled = _totalEnergyWhScaled;
        emit EnergyReported(_totalEnergyWhScaled, currentTime);
    } 

    function getHistoryCount() public view returns (uint256) {
        return history.length;
    }
}