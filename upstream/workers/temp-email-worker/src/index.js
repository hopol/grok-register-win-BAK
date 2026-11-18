import ADMIN_HTML from "./admin.html";
import { decodeMimeWords, decodedMimeBodies, extractOTP } from "./otp.js";

const DOMAINS = new Set(["liziai.cloud", "edu.liziai.cloud"]);
const MAX_MESSAGES_PER_MAILBOX = 100;
const MAX_RAW_LENGTH = 1_500_000;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function isAuthorized(request, env) {
  const expected = String(env.ADMIN_TOKEN || "").trim();
  if (!expected) return false;
  const direct = String(request.headers.get("X-Admin-Token") || "").trim();
  const bearer = String(request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "").trim();
  return direct === expected || bearer === expected;
}

async function readMailbox(env, address) {
  const stored = await env.EMAILS.get(`email:${address}`, "json");
  if (Array.isArray(stored)) return stored;
  return stored && typeof stored === "object" ? [stored] : [];
}

async function writeMailbox(env, address, messages) {
  await env.EMAILS.put(`email:${address}`, JSON.stringify(messages.slice(0, MAX_MESSAGES_PER_MAILBOX)));
  const current = await env.EMAILS.get("index:mailboxes", "json");
  const mailboxes = new Set(Array.isArray(current) ? current : []);
  mailboxes.add(address);
  await env.EMAILS.put("index:mailboxes", JSON.stringify([...mailboxes].sort()));
}

async function storeEmail(message, env) {
  const toAddress = String(message.to || "").trim().toLowerCase();
  const fromAddress = String(message.from || "").trim();
  if (!toAddress || !toAddress.includes("@")) throw new Error("Incoming email has no recipient");
  const raw = (await new Response(message.raw).text()).slice(0, MAX_RAW_LENGTH);
  const subject = decodeMimeWords(message.headers.get("subject") || "");
  const decodedBodies = decodedMimeBodies(raw);
  const otp = extractOTP(subject, ...decodedBodies);
  const now = Date.now();
  const messageId = String(message.headers.get("message-id") || crypto.randomUUID()).replace(/[<>]/g, "");
  const record = {
    id: crypto.randomUUID(),
    message_id: messageId,
    to_address: toAddress,
    from_address: fromAddress,
    subject,
    body: decodedBodies.slice(1).join("\n\n") || raw,
    bodyPreview: raw.slice(0, 5000),
    raw_text: raw,
    extracted_json: JSON.stringify(otp ? [{ value: otp }] : []),
    received_at: now,
    receivedDateTime: new Date(now).toISOString(),
  };
  const messages = await readMailbox(env, toAddress);
  messages.unshift(record);
  await writeMailbox(env, toAddress, messages);
  console.log(`stored email recipient=${toAddress} otp=${otp ? "yes" : "no"}`);
}

async function allMessages(env, limit) {
  const listed = await env.EMAILS.list({ prefix: "email:", limit: 1000 });
  const groups = await Promise.all(listed.keys.map((key) => env.EMAILS.get(key.name, "json")));
  const messages = groups.flatMap((value) => (Array.isArray(value) ? value : value ? [value] : []));
  messages.sort((left, right) => Number(right.received_at || 0) - Number(left.received_at || 0));
  return messages.slice(0, limit);
}

async function handleAdminAll(request, env) {
  if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
  const url = new URL(request.url);
  const limit = Math.max(1, Math.min(Number(url.searchParams.get("limit") || 100), 500));
  const messages = await allMessages(env, limit);
  const mailboxes = [...new Set(messages.map((item) => item.to_address).filter(Boolean))];
  return json({ messages, mailboxes, total: messages.length });
}

async function handleAdminMessage(request, env) {
  if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
  const url = new URL(request.url);
  const address = String(url.searchParams.get("email") || "").trim().toLowerCase();
  const id = String(url.searchParams.get("id") || "");
  const messages = await readMailbox(env, address);
  const found = messages.find((item) => String(item.id) === id || String(item.message_id) === id);
  return found ? json({ message: found }) : json({ error: "message not found" }, 404);
}

async function handleAdminDelete(request, env) {
  if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
  const url = new URL(request.url);
  const address = String(url.searchParams.get("email") || "").trim().toLowerCase();
  const id = String(url.searchParams.get("id") || "");
  const messages = await readMailbox(env, address);
  await writeMailbox(env, address, messages.filter((item) => String(item.id) !== id && String(item.message_id) !== id));
  return json({ ok: true });
}

async function handleCreateMailboxes(request, env) {
  if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
  const data = await request.json().catch(() => ({}));
  const domain = String(data.domain || "").trim().toLowerCase();
  if (!DOMAINS.has(domain)) return json({ error: "unsupported domain" }, 400);
  const count = Math.max(1, Math.min(Number(data.count || 1), 50));
  const emails = Array.from({ length: count }, () => `oai-${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}@${domain}`);
  return json({ ok: true, emails, data: { emails } });
}

async function handleFetch(request, env) {
  const url = new URL(request.url);
  if (url.pathname === "/") {
    return new Response(ADMIN_HTML, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
  }
  if (url.pathname === "/api/mailboxes" && request.method === "POST") return handleCreateMailboxes(request, env);
  if (url.pathname === "/admin/all" && request.method === "GET") return handleAdminAll(request, env);
  if (url.pathname === "/admin/msg" && request.method === "GET") return handleAdminMessage(request, env);
  if (url.pathname === "/admin/delete" && request.method === "DELETE") return handleAdminDelete(request, env);
  return json({ error: "not found" }, 404);
}

export default {
  fetch: handleFetch,
  async email(message, env, ctx) {
    ctx.waitUntil(storeEmail(message, env));
  },
};
