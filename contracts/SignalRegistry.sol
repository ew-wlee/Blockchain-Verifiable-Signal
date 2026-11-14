// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title SignalRegistry
/// @notice Stores one immutable version commitment per objectId. No raw data,
/// model weights, funds, admin controls, or upgrade hooks are stored here.
contract SignalRegistry {
    struct SignalRecord {
        address provider;
        bytes32 dataHash;
        bytes32 modelHash;
        bytes32 signalHash;
        bytes32 manifestHash;
        uint64 timestamp;
    }

    mapping(bytes32 => SignalRecord) private _records;
    mapping(bytes32 => bool) private _registered;

    error SignalAlreadyRegistered(bytes32 objectId);
    error SignalNotFound(bytes32 objectId);
    error ZeroValue(string field);

    event SignalRegistered(
        bytes32 indexed objectId,
        address indexed provider,
        bytes32 dataHash,
        bytes32 modelHash,
        bytes32 signalHash,
        bytes32 manifestHash,
        uint64 timestamp
    );

    /// @notice Register a frozen bundle commitment exactly once.
    function registerSignal(
        bytes32 objectId,
        bytes32 dataHash,
        bytes32 modelHash,
        bytes32 signalHash,
        bytes32 manifestHash
    ) external {
        if (objectId == bytes32(0)) revert ZeroValue("objectId");
        if (dataHash == bytes32(0)) revert ZeroValue("dataHash");
        if (modelHash == bytes32(0)) revert ZeroValue("modelHash");
        if (signalHash == bytes32(0)) revert ZeroValue("signalHash");
        if (manifestHash == bytes32(0)) revert ZeroValue("manifestHash");
        if (_registered[objectId]) revert SignalAlreadyRegistered(objectId);

        uint64 registeredAt = uint64(block.timestamp);
        _records[objectId] = SignalRecord({
            provider: msg.sender,
            dataHash: dataHash,
            modelHash: modelHash,
            signalHash: signalHash,
            manifestHash: manifestHash,
            timestamp: registeredAt
        });
        _registered[objectId] = true;

        emit SignalRegistered(
            objectId,
            msg.sender,
            dataHash,
            modelHash,
            signalHash,
            manifestHash,
            registeredAt
        );
    }

    /// @notice Read a registered record. Unregistered IDs revert explicitly.
    function getSignal(bytes32 objectId) external view returns (SignalRecord memory) {
        if (!_registered[objectId]) revert SignalNotFound(objectId);
        return _records[objectId];
    }
}
