#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
const rootDir = process.cwd();
const desired = JSON.parse(
  readFileSync(path.join(rootDir, "deploy/dokploy.desired-state.json"), "utf8"),
);
const compose = readFileSync(path.join(rootDir, "deploy/docker-compose.yml"), "utf8");
const stageDesired = JSON.parse(
  readFileSync(path.join(rootDir, "deploy/dokploy.stage.desired-state.json"), "utf8"),
);
const stageCompose = readFileSync(path.join(rootDir, "deploy/dokploy.stage.yml"), "utf8");
assert.equal(desired.composeId, "kl7tmNAE6_kbE_c7q6l2I");
assert.equal(desired.sourceType, "git");
assert.equal(desired.customGitUrl, "https://github.com/viewport-corp/fork-hermes-agent.git");
assert.equal(desired.customGitBranch, "main");
assert.equal(desired.composePath, "deploy/docker-compose.yml");
assert.equal(desired.composeType, "docker-compose");
assert.equal(desired.secrets.source, "/srv/viewport/secrets/platformx.env");
assert.equal(desired.secrets.persistRegistryCredentialsInDokploy, false);
assert.match(
  desired.secrets.dashboardAuthMigrationPrerequisite,
  /must contain one dashboard auth method/u,
);
assert.equal(desired.upstreamReleaseCommit, "5fc308a70719a83cccdbba4c0e39c23f5a8239d5");
assert.equal(desired.network.external, true);
assert.equal(desired.network.name, "fork-hermes-agent_default");
assert.equal(desired.network.gatewayIpv4Address, "172.31.15.2");
assert.ok(
  compose.includes(
    "x-hermes-image: &hermes-image ${HERMES_IMAGE:?set immutable " +
      "ghcr.io/viewport-corp/fork-hermes-agent@sha256:<digest>",
  ),
);
assert.match(compose, /source: \/srv\/viewport\/secrets\/platformx\.env/u);
assert.match(compose, /target: \/run\/hermes-secrets/u);
assert.match(compose, /pull_policy: if_not_present/u);
assert.match(compose, /ipv4_address: \$\{HERMES_IPV4_ADDRESS:-172\.31\.15\.2\}/u);
assert.match(compose, /name: \$\{HERMES_NETWORK_NAME:-fork-hermes-agent_default\}/u);
assert.match(compose, /http:\/\/127\.0\.0\.1:9119\/api\/health/u);
assert.doesNotMatch(
  compose,
  /HERMES_GIT_SHA=5fc308a70719a83cccdbba4c0e39c23f5a8239d5/u,
);
assert.match(
  compose,
  /viewport\.hermes\.upstream_release_commit=5fc308a70719a83cccdbba4c0e39c23f5a8239d5/u,
);
assert.ok(
  compose.includes(
    "viewport.hermes.image_source_revision=" +
      "${HERMES_IMAGE_SOURCE_REVISION:-set-by-ghcr-publish-digest}",
  ),
);
assert.doesNotMatch(compose, /\n    env_file:\n/u);
assert.doesNotMatch(compose, /\n    build:\n/u);
assert.doesNotMatch(compose, /\n    deploy:\n/u);

assert.equal(stageDesired.stageOnly, true);
assert.equal(stageDesired.composePath, "deploy/dokploy.stage.yml");
assert.equal(stageDesired.secrets.projectorProfile, "stage");
assert.deepEqual(stageDesired.secrets.allowedProjectedKeys, []);
assert.equal(stageDesired.secrets.emptyProjectionAllowed, true);
for (const key of [
  "HERMES_STAGE_PROJECT_NAME",
  "HERMES_STAGE_CONTAINER_NAME",
  "HERMES_STAGE_STATE_DIR",
  "HERMES_STAGE_SECRET_VOLUME_NAME",
  "HERMES_STAGE_NETWORK_NAME",
  "HERMES_STAGE_SUBNET",
  "HERMES_STAGE_IPV4_ADDRESS",
  "HERMES_STAGE_IMAGE",
]) {
  assert.ok(stageDesired.requiredVariables.includes(key), `missing stage var ${key}`);
}
assert.ok(
  stageCompose.includes(
    "name: ${HERMES_STAGE_SECRET_VOLUME_NAME:?set isolated stage secret volume name}",
  ),
);
assert.match(stageCompose, /container_name: \$\{HERMES_STAGE_CONTAINER_NAME:/u);
assert.match(stageCompose, /source: \$\{HERMES_STAGE_STATE_DIR:/u);
assert.ok(
  stageCompose.includes(
    "name: ${HERMES_STAGE_NETWORK_NAME:?set isolated stage network name}",
  ),
);
assert.ok(
  stageCompose.includes(
    "name: ${HERMES_STAGE_PROJECT_NAME:?set isolated Dokploy stage project name}",
  ),
);
assert.match(stageCompose, /subnet: \$\{HERMES_STAGE_SUBNET:/u);
assert.match(stageCompose, /ipv4_address: \$\{HERMES_STAGE_IPV4_ADDRESS:/u);
assert.match(stageCompose, /project-platformx-env\.mjs .* stage/u);
assert.match(stageCompose, /HERMES_DASHBOARD_HOST=127\.0\.0\.1/u);
assert.match(stageCompose, /http:\/\/127\.0\.0\.1:9119\/api\/health/u);
assert.doesNotMatch(stageCompose, /ports:/u);
assert.doesNotMatch(stageCompose, /172\.31\.15\.2/u);
assert.doesNotMatch(stageCompose, /hermes-viewport-new/u);
assert.doesNotMatch(stageCompose, /TELEGRAM_/u);
assert.doesNotMatch(
  stageCompose,
  /OPENAI_API_KEY|ANTHROPIC_API_KEY|OPENROUTER_API_KEY|GITHUB_TOKEN/u,
);

const pullPolicyCount = (
  compose.match(/\n    pull_policy: if_not_present\n/gu) ?? []
).length;
assert.equal(pullPolicyCount, 2);
process.stdout.write(
  JSON.stringify({
    dokployDesiredStateSafe: true,
    composeId: desired.composeId,
  }) + "\n",
);
