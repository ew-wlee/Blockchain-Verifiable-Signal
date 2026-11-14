import { expect } from "chai";
import { ethers } from "hardhat";

const ZERO = ethers.ZeroHash;

function hash(label: string): string {
  return ethers.id(label);
}

async function deployRegistry(): Promise<any> {
  const factory = await ethers.getContractFactory("SignalRegistry");
  const registry = await factory.deploy();
  await registry.waitForDeployment();
  return registry as any;
}

function hashes(prefix: string) {
  return {
    dataHash: hash(`${prefix}:data`),
    modelHash: hash(`${prefix}:model`),
    signalHash: hash(`${prefix}:signal`),
    manifestHash: hash(`${prefix}:manifest`),
  };
}

describe("SignalRegistry", function () {
  it("registers once and returns every field with the correct event", async function () {
    const [provider] = await ethers.getSigners();
    const registry = await deployRegistry();
    const objectId = hash("object:one");
    const expected = hashes("object:one");

    const tx = await registry.registerSignal(
      objectId,
      expected.dataHash,
      expected.modelHash,
      expected.signalHash,
      expected.manifestHash
    );
    const receipt = await tx.wait();
    const block = await ethers.provider.getBlock(receipt!.blockNumber);
    expect(block).to.not.equal(null);

    const record = await registry.getSignal(objectId);
    expect(record.provider).to.equal(provider.address);
    expect(record.dataHash).to.equal(expected.dataHash);
    expect(record.modelHash).to.equal(expected.modelHash);
    expect(record.signalHash).to.equal(expected.signalHash);
    expect(record.manifestHash).to.equal(expected.manifestHash);
    expect(record.timestamp).to.equal(BigInt(block!.timestamp));

    const event = receipt!.logs
      .map((log: any) => {
        try {
          return registry.interface.parseLog(log);
        } catch {
          return null;
        }
      })
      .find((parsed: any) => parsed?.name === "SignalRegistered");
    expect(event).to.not.equal(undefined);
    expect(event!.args.objectId).to.equal(objectId);
    expect(event!.args.provider).to.equal(provider.address);
    expect(event!.args.dataHash).to.equal(expected.dataHash);
    expect(event!.args.modelHash).to.equal(expected.modelHash);
    expect(event!.args.signalHash).to.equal(expected.signalHash);
    expect(event!.args.manifestHash).to.equal(expected.manifestHash);
    expect(event!.args.timestamp).to.equal(BigInt(block!.timestamp));
  });

  it("rejects a duplicate objectId and preserves the original record", async function () {
    const registry = await deployRegistry();
    const objectId = hash("object:duplicate");
    const original = hashes("object:duplicate");
    const replacement = hashes("object:replacement");

    await registry.registerSignal(
      objectId,
      original.dataHash,
      original.modelHash,
      original.signalHash,
      original.manifestHash
    );
    const before = await registry.getSignal(objectId);

    await expect(
      registry.registerSignal(
        objectId,
        replacement.dataHash,
        replacement.modelHash,
        replacement.signalHash,
        replacement.manifestHash
      )
    )
      .to.be.revertedWithCustomError(registry, "SignalAlreadyRegistered")
      .withArgs(objectId);

    const after = await registry.getSignal(objectId);
    expect(after.provider).to.equal(before.provider);
    expect(after.dataHash).to.equal(before.dataHash);
    expect(after.modelHash).to.equal(before.modelHash);
    expect(after.signalHash).to.equal(before.signalHash);
    expect(after.manifestHash).to.equal(before.manifestHash);
    expect(after.timestamp).to.equal(before.timestamp);
  });

  it("isolates records for different objectIds", async function () {
    const registry = await deployRegistry();
    const firstId = hash("object:first");
    const secondId = hash("object:second");
    const first = hashes("object:first");
    const second = hashes("object:second");

    await registry.registerSignal(
      firstId,
      first.dataHash,
      first.modelHash,
      first.signalHash,
      first.manifestHash
    );
    await registry.registerSignal(
      secondId,
      second.dataHash,
      second.modelHash,
      second.signalHash,
      second.manifestHash
    );

    const firstRecord = await registry.getSignal(firstId);
    const secondRecord = await registry.getSignal(secondId);
    expect(firstRecord.dataHash).to.equal(first.dataHash);
    expect(secondRecord.dataHash).to.equal(second.dataHash);
    expect(firstRecord.manifestHash).to.equal(first.manifestHash);
    expect(secondRecord.manifestHash).to.equal(second.manifestHash);
    expect(firstRecord.dataHash).to.not.equal(secondRecord.dataHash);
  });

  it("reverts explicitly for an unregistered objectId", async function () {
    const registry = await deployRegistry();
    const objectId = hash("object:missing");
    await expect(registry.getSignal(objectId))
      .to.be.revertedWithCustomError(registry, "SignalNotFound")
      .withArgs(objectId);
  });

  it("stores the actual msg.sender as provider for different accounts", async function () {
    const [firstProvider, secondProvider] = await ethers.getSigners();
    const registry = await deployRegistry();
    const firstId = hash("provider:first");
    const secondId = hash("provider:second");
    const first = hashes("provider:first");
    const second = hashes("provider:second");

    await registry
      .connect(firstProvider)
      .registerSignal(
        firstId,
        first.dataHash,
        first.modelHash,
        first.signalHash,
        first.manifestHash
      );
    await registry
      .connect(secondProvider)
      .registerSignal(
        secondId,
        second.dataHash,
        second.modelHash,
        second.signalHash,
        second.manifestHash
      );

    expect((await registry.getSignal(firstId)).provider).to.equal(
      firstProvider.address
    );
    expect((await registry.getSignal(secondId)).provider).to.equal(
      secondProvider.address
    );
  });

  it("rejects zero objectId and each zero hash with a named error", async function () {
    const registry = await deployRegistry();
    const objectId = hash("object:zero");
    const values = hashes("object:zero");
    const cases: Array<[string, string[]]> = [
      ["objectId", [ZERO, values.dataHash, values.modelHash, values.signalHash, values.manifestHash]],
      ["dataHash", [objectId, ZERO, values.modelHash, values.signalHash, values.manifestHash]],
      ["modelHash", [objectId, values.dataHash, ZERO, values.signalHash, values.manifestHash]],
      ["signalHash", [objectId, values.dataHash, values.modelHash, ZERO, values.manifestHash]],
      ["manifestHash", [objectId, values.dataHash, values.modelHash, values.signalHash, ZERO]],
    ];

    for (const [field, args] of cases) {
      await expect(
        registry.registerSignal(
          args[0],
          args[1],
          args[2],
          args[3],
          args[4]
        )
      )
        .to.be.revertedWithCustomError(registry, "ZeroValue")
        .withArgs(field);
    }
  });
});
