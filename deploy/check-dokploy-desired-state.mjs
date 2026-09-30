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
const officialImage = /^docker\.io\/nousresearch\/hermes-agent:v\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?@sha256:[a-f0-9]{64}$/u;
const liveStateDir = "/srv/viewport/runtime/hermes-viewport-v2026-8-27";

// Production desired state.
assert.equal(desired.composeId, "kl7tmNAE6_kbE_c7q6l2I");
assert.equal(desired.sourceType, "git");
assert.equal(desired.customGitUrl, "https://github.com/viewport-corp/fork-hermes-agent.git");
assert.equal(desired.customGitBranch, "main");
assert.equal(desired.composePath, "deploy/docker-compose.yml");
assert.equal(desired.composeType, "docker-compose");
assert.match(desired.image, officialImage);
assert.ok(desired.image.includes(`:${desired.upstreamRelease}@`), "image tag must equal upstreamRelease");
assert.match(desired.upstreamReleaseCommit, /^[a-f0-9]{40}$/u);
assert.equal(desired.stateDir, liveStateDir);
assert.equal(desired.secrets.persistRegistryCredentialsInDokploy, false);
assert.equal(desired.secrets.telegramBotToken.dokployEnvName, "HERMES_TELEGRAM_BOT_TOKEN");
assert.equal(desired.secrets.telegramBotToken.containerEnvName, "TELEGRAM_BOT_TOKEN");
assert.ok(desired.secrets.requiredEnv.includes("HERMES_TELEGRAM_BOT_TOKEN"));
assert.ok(!desired.secrets.requiredEnv.includes("TELEGRAM_BOT_TOKEN"));
assert.equal(desired.legacyStateEnv.policy, "keep");
assert.equal(desired.dashboard.hostPublish, "127.0.0.1:9119:9119");
assert.equal(desired.network.mode, "bridge");
assert.ok(
  desired.prerequisitesBeforeDeploy[0].includes(
    `sourceType=git, customGitUrl=${desired.customGitUrl}, customGitBranch=${desired.customGitBranch}, composePath=${desired.composePath}`,
  ),
  "first deploy prerequisite must switch the Dokploy record to the Git source",
);
assert.match(desired.rollback.backupDir, /^\/srv\/viewport\/backups\//u);
assert.ok(
  desired.rollback.backupBeforeDeploy.some((step) => step.includes(`${liveStateDir}/ ${desired.rollback.backupDir}/state/`)),
  "rollback must back up the live state folder",
);
assert.doesNotMatch(JSON.stringify(desired), /hermes-viewport-new|:latest|<replace/u);

// Production compose: official image, live state, own bot token, upstream entrypoint.
assert.ok(compose.includes(`x-hermes-image: &hermes-image ${desired.image}\n`));
assert.ok(compose.includes(`viewport.hermes.release=${desired.upstreamRelease}`));
assert.ok(compose.includes(`viewport.hermes.upstream_release_commit=${desired.upstreamReleaseCommit}`));
assert.ok(compose.includes(`source: ${liveStateDir}\n        target: /opt/data\n`));
assert.doesNotMatch(compose, /hermes-viewport-new|:latest\b/u);
assert.match(compose, /\n      TELEGRAM_BOT_TOKEN: \$\{HERMES_TELEGRAM_BOT_TOKEN:\?/u);
assert.doesNotMatch(compose, /\$\{TELEGRAM_BOT_TOKEN/u);
assert.match(compose, /\n      LITELLM_HERMES_KEY: \$\{LITELLM_HERMES_KEY:\?/u);
for (const name of desired.secrets.requiredEnv.filter((key) => key !== "HERMES_TELEGRAM_BOT_TOKEN")) {
  assert.ok(compose.includes(`\n      ${name}: \${${name}:?`), `compose must require ${name}`);
}
for (const name of desired.secrets.optionalEnv) {
  assert.ok(compose.includes(`\n      ${name}: \${${name}:-}\n`), `compose must pass optional ${name}`);
}
assert.match(compose, /HERMES_UID: "10000"/u);
assert.match(compose, /\n      - "127\.0\.0\.1:9119:9119"\n/u);
assert.match(compose, /\n    network_mode: bridge\n/u);
assert.match(compose, /source: \/run\/docker-viewport\.sock\n        target: \/var\/run\/docker\.sock\n/u);
assert.match(compose, /http:\/\/127\.0\.0\.1:9119\/api\/health/u);
assert.doesNotMatch(compose, /source: \/srv\/viewport\/secrets|secret-init|hermes-entrypoint\.sh|project-platformx-env/u);
assert.doesNotMatch(compose, /\n    (entrypoint|user|env_file|build|deploy):/u);
assert.equal((compose.match(/\n    pull_policy: if_not_present\n/gu) ?? []).length, 1);

// Stage: official image, no secrets, isolated, guarded against copied legacy env files.
assert.equal(stageDesired.stageOnly, true);
assert.equal(stageDesired.composePath, "deploy/dokploy.stage.yml");
assert.match(stageDesired.image, officialImage);
assert.equal(stageDesired.secrets.source, "none");
assert.deepEqual(stageDesired.secrets.allowedProjectedKeys, []);
assert.equal(stageDesired.runtimeIsolation.forbiddenProductionStateDir, liveStateDir);
assert.equal(stageDesired.runtimeIsolation.legacyEnvExclusion.required, true);
assert.deepEqual(stageDesired.runtimeIsolation.legacyEnvExclusion.forbiddenFiles, [".op.env"]);
assert.match(stageDesired.runtimeIsolation.legacyEnvExclusion.envFilePolicy, /forbiddenProductionSecretClasses/u);
assert.match(stageDesired.runtimeIsolation.legacyEnvExclusion.reason, /entrypoint guard fails closed/u);
for (const key of [
  "HERMES_STAGE_PROJECT_NAME",
  "HERMES_STAGE_CONTAINER_NAME",
  "HERMES_STAGE_STATE_DIR",
  "HERMES_STAGE_NETWORK_NAME",
  "HERMES_STAGE_SUBNET",
  "HERMES_STAGE_IPV4_ADDRESS",
  "HERMES_STAGE_IMAGE",
]) {
  assert.ok(stageDesired.requiredVariables.includes(key), `missing stage var ${key}`);
  assert.ok(stageCompose.includes(`\${${key}:?`), `stage compose must require ${key}`);
}
assert.ok(
  stageCompose.includes(
    "name: ${HERMES_STAGE_PROJECT_NAME:?set isolated Dokploy stage project name}",
  ),
);
assert.ok(
  stageCompose.includes(
    "name: ${HERMES_STAGE_NETWORK_NAME:?set isolated stage network name}",
  ),
);
// Stage guard: a literal block between "- |" and "- hermes-stage-guard" in the entrypoint.
const stageGuardBlock = stageCompose.match(/\n      - \|\n((?:        .*\n)+)      - hermes-stage-guard\n/u);
assert.ok(stageGuardBlock, "stage entrypoint guard block missing");
const stageGuard = stageGuardBlock[1];
assert.match(stageGuard, /if \[ -e \/opt\/data\/\.op\.env \] \|\| \[ -L \/opt\/data\/\.op\.env \]; then\n.*\n          exit 78\n/u);
assert.match(stageGuard, /if \[ -L \/opt\/data\/\.env \]; then\n.*\n          exit 78\n/u);
assert.match(stageGuard, /if \[ -f \/opt\/data\/\.env \] && grep -Eq ".*" \/opt\/data\/\.env; then\n.*\n          exit 78\n/u);
const forbiddenKeyAlternation = stageDesired.secrets.forbiddenProductionSecretClasses
  .map((keyClass) => (keyClass.endsWith("*") ? `${keyClass.slice(0, -1)}[A-Z0-9_]*` : keyClass))
  .join("|");
assert.ok(
  stageGuard.includes(`(export[[:space:]]+)?(${forbiddenKeyAlternation})[[:space:]]*=`),
  "stage guard must refuse every forbiddenProductionSecretClasses key",
);
assert.ok(stageGuard.endsWith('        exec /opt/hermes/docker/entrypoint-dispatch.sh "$$@"\n'));
assert.match(stageCompose, /HERMES_DASHBOARD_HOST=127\.0\.0\.1/u);
assert.match(stageCompose, /http:\/\/127\.0\.0\.1:9119\/api\/health/u);
assert.doesNotMatch(stageCompose, /ports:/u);
assert.doesNotMatch(stageCompose, /hermes-viewport-new|hermes-viewport-v2026-8-27/u);
assert.doesNotMatch(stageCompose, /platformx\.env|secret-init|env_file|docker\.sock/u);
// Secret names may appear only inside the guard, which names them to refuse them.
const stageComposeOutsideGuard = stageCompose.replace(stageGuardBlock[0], "\n");
assert.doesNotMatch(stageComposeOutsideGuard, /TELEGRAM_/u);
assert.doesNotMatch(
  stageComposeOutsideGuard,
  /OPENAI_API_KEY|ANTHROPIC_API_KEY|OPENROUTER_API_KEY|GITHUB_TOKEN|LITELLM_/u,
);

process.stdout.write(
  JSON.stringify({
    dokployDesiredStateSafe: true,
    composeId: desired.composeId,
  }) + "\n",
);
