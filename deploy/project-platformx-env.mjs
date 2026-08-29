#!/usr/bin/env node
import { chmodSync, chownSync, closeSync, constants, openSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import process from "node:process";
const sourcePath = process.argv[2];
const destinationPath = process.argv[3];
const profile = process.argv[4] ?? "production";
if (!sourcePath || !destinationPath) throw new Error("usage: project-platformx-env.mjs SOURCE DESTINATION [production|stage]");
if (profile !== "production" && profile !== "stage") throw new Error(`unsupported secret profile: ${profile}`);
const gatewayKeys = ["API_SERVER_KEY", "HERMES_AUTH_JSON_BOOTSTRAP", "HERMES_AUTH_JSON_REBOOTSTRAP", "HERMES_DASHBOARD_BASIC_AUTH_USERNAME", "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH", "HERMES_DASHBOARD_BASIC_AUTH_SECRET", "HERMES_DASHBOARD_DRAIN_SECRET", "HERMES_DASHBOARD_OAUTH_CLIENT_ID", "HERMES_DASHBOARD_OAUTH_CLIENT_SECRET", "HERMES_DASHBOARD_OIDC_ISSUER", "HERMES_DASHBOARD_OIDC_CLIENT_ID", "HERMES_DASHBOARD_OIDC_CLIENT_SECRET", "HERMES_DASHBOARD_SESSION_TOKEN", "HERMES_INFERENCE_MODEL", "HERMES_INFERENCE_PROVIDER"];
const modelKeys = ["ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GITHUB_TOKEN", "GITHUB_TOKEN_VIEWPORT_CORP", "GOOGLE_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY"];
const telegramKeys = ["TELEGRAM_ALLOWED_USERS", "TELEGRAM_ALLOW_ALL_USERS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CRON_THREAD_ID", "TELEGRAM_FALLBACK_IPS", "TELEGRAM_GROUP_ALLOWED_CHATS", "TELEGRAM_GROUP_ALLOWED_USERS", "TELEGRAM_HOME_CHANNEL", "TELEGRAM_HOME_CHANNEL_NAME", "TELEGRAM_HOME_CHANNEL_THREAD_ID", "TELEGRAM_PROXY", "TELEGRAM_REQUIRE_MENTION"];
const stageKeys = [];
const allowedKeys = profile === "production" ? [...gatewayKeys, ...modelKeys, ...telegramKeys] : stageKeys;
const parseEnv = (content) => {
  const out = {};
  for (const rawLine of content.split(/\r?\n/u)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const index = line.indexOf("=");
    if (index <= 0) continue;
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if (!/^[A-Z][A-Z0-9_]{0,127}$/u.test(key)) continue;
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
};
const parsed = parseEnv(readFileSync(sourcePath, "utf8"));
const selected = Object.fromEntries(allowedKeys.filter((key) => typeof parsed[key] === "string" && parsed[key].length > 0).map((key) => [key, parsed[key]]));
if (profile === "production" && !selected.TELEGRAM_BOT_TOKEN) throw new Error("canonical secret source lacks TELEGRAM_BOT_TOKEN for production");
const hasDashboardAuth = Boolean((selected.HERMES_DASHBOARD_BASIC_AUTH_USERNAME && (selected.HERMES_DASHBOARD_BASIC_AUTH_PASSWORD || selected.HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH)) || selected.HERMES_DASHBOARD_OAUTH_CLIENT_ID || (selected.HERMES_DASHBOARD_OIDC_ISSUER && selected.HERMES_DASHBOARD_OIDC_CLIENT_ID));
if (profile === "production" && !hasDashboardAuth) throw new Error("canonical secret source lacks dashboard auth migration keys: set basic auth username plus password/hash, or OAuth client id, or OIDC issuer plus client id");
if (!selected.GITHUB_TOKEN && selected.GITHUB_TOKEN_VIEWPORT_CORP) selected.GITHUB_TOKEN = selected.GITHUB_TOKEN_VIEWPORT_CORP;
const quoteForShell = (value) => { if (value.includes("\0")) throw new Error("secret values must not contain NUL bytes"); return `'${value.replaceAll("'", "'\\''")}'`; };
const content = `${Object.keys(selected).sort().map((key) => `export ${key}=${quoteForShell(selected[key])}`).join("\n")}\n`;
const temporaryPath = `${destinationPath}.${process.pid}.tmp`;
const descriptor = openSync(temporaryPath, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o400);
try { writeFileSync(descriptor, content, { encoding: "utf8" }); } finally { closeSync(descriptor); }
chmodSync(temporaryPath, 0o400);
if (process.getuid?.() === 0) chownSync(temporaryPath, 10000, 10000);
renameSync(temporaryPath, destinationPath);
process.stdout.write(`${JSON.stringify({ profile, projectedKeys: Object.keys(selected).sort(), destinationPath })}\n`);
