import { expect } from "chai";

import {
  EXPECTED_STEP2,
  canonicalJson,
  loadFrozenStep2,
  objectIdFromManifest,
  sha256File,
  STEP2_BUNDLE,
} from "../scripts/lib";
import fs from "node:fs";
import path from "node:path";

describe("Frozen Step 2 registration inputs", function () {
  it("matches the reviewed objectId and four on-chain hashes", function () {
    const frozen = loadFrozenStep2();
    expect(frozen.objectId).to.equal(EXPECTED_STEP2.objectId);
    expect(frozen.dataHash).to.equal(EXPECTED_STEP2.dataHash);
    expect(frozen.modelHash).to.equal(EXPECTED_STEP2.modelHash);
    expect(frozen.signalHash).to.equal(EXPECTED_STEP2.signalHash);
    expect(frozen.manifestHash).to.equal(EXPECTED_STEP2.manifestHash);
    expect(frozen.configHash).to.equal(EXPECTED_STEP2.configHash);
  });

  it("recomputes objectId from the canonical manifest identity view", function () {
    const manifest = JSON.parse(
      fs.readFileSync(path.join(STEP2_BUNDLE, "manifest.json"), "utf8")
    ) as Record<string, unknown>;
    expect(objectIdFromManifest(manifest)).to.equal(EXPECTED_STEP2.objectId);
    expect(canonicalJson({ b: 2, a: 1 })).to.equal('{"a":1,"b":2}\n');
  });

  it("contains exactly six frozen bundle files", function () {
    const files = fs
      .readdirSync(STEP2_BUNDLE)
      .filter((name) => fs.statSync(path.join(STEP2_BUNDLE, name)).isFile())
      .sort();
    expect(files).to.deep.equal(
      [
        "backtest_summary.json",
        "config.json",
        "dataset.csv",
        "manifest.json",
        "model_reference.json",
        "signals.csv",
      ].sort()
    );
    expect(sha256File(path.join(STEP2_BUNDLE, "manifest.json"))).to.equal(
      EXPECTED_STEP2.manifestHash.slice(2)
    );
  });
});
