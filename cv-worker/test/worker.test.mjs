// Tests du Worker sans appeler OpenAI (fetch simulé).   node --test cv-worker/test
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

const ORIGIN = "https://pierridotite.github.io";
const LEX = { skills: ["python", "r", "sql"], domains: ["agri", "sport"], roles: ["ds", "stat"], languages: ["es"] };
const CV_TEXT = "Élève ingénieur en data science. ".repeat(20);
const PROFILE = { typology: "Biostatisticien agro", summary: "Profil statistique.", skills: ["r"],
  domains: [{ id: "agri", strength: "forte" }], target_roles: ["stat"], languages: [], experience_months: 6 };

function env(extra = {}) {
  return { OPENAI_API_KEY: "test", ALLOWED_ORIGINS: ORIGIN, MODEL: "gpt-5-nano", FALLBACK_MODEL: "gpt-4.1-nano", ...extra };
}
function req(body, origin = ORIGIN, method = "POST") {
  return new Request("https://w.example/", { method, headers: { Origin: origin, "Content-Type": "application/json" },
    body: method === "POST" ? JSON.stringify(body) : undefined });
}
function mockOpenAI({ missing } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push(body);
    if (body.model === missing) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(PROFILE) } }] }), { status: 200 });
  };
  return calls;
}

test("refuse une origine inconnue", async () => {
  mockOpenAI();
  const r = await worker.fetch(req({ text: CV_TEXT, lexicon: LEX }, "https://evil.example"), env());
  assert.equal(r.status, 403);
});

test("répond au préflight CORS du site", async () => {
  const r = await worker.fetch(req(null, ORIGIN, "OPTIONS"), env());
  assert.equal(r.status, 204);
  assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);
});

test("analyse un CV avec un schéma strict limité au dictionnaire", async () => {
  const calls = mockOpenAI();
  const r = await worker.fetch(req({ text: CV_TEXT, lexicon: LEX }), env());
  assert.equal(r.status, 200);
  assert.deepEqual((await r.json()).profile, PROFILE);
  const body = calls[0];
  assert.equal(body.response_format.json_schema.strict, true);
  assert.deepEqual(body.response_format.json_schema.schema.properties.skills.items.enum, LEX.skills);
  assert.equal(body.reasoning_effort, "minimal");
});

test("tronque un CV trop long et refuse un CV vide", async () => {
  const calls = mockOpenAI();
  await worker.fetch(req({ text: "x".repeat(50000), lexicon: LEX }), env());
  assert.ok(calls[0].messages[1].content.length <= 15000 + 10);
  const r = await worker.fetch(req({ text: "trop court", lexicon: LEX }), env());
  assert.equal(r.status, 400);
});

test("ignore les identifiants invalides du dictionnaire", async () => {
  const calls = mockOpenAI();
  await worker.fetch(req({ text: CV_TEXT, lexicon: { ...LEX, skills: ["python", "<script>", 42] } }), env());
  assert.deepEqual(calls[0].response_format.json_schema.schema.properties.skills.items.enum, ["python"]);
});

test("bascule sur le modèle de secours", async () => {
  const calls = mockOpenAI({ missing: "gpt-5-nano" });
  const r = await worker.fetch(req({ text: CV_TEXT, lexicon: LEX }), env());
  assert.equal((await r.json()).model, "gpt-4.1-nano");
  assert.equal(calls.length, 2);
});

test("applique la limite de débit", async () => {
  mockOpenAI();
  const r = await worker.fetch(req({ text: CV_TEXT, lexicon: LEX }), env({ CV_RATE_LIMITER: { limit: async () => ({ success: false }) } }));
  assert.equal(r.status, 429);
});

test("sans clé, le service se déclare non configuré", async () => {
  const r = await worker.fetch(req({ text: CV_TEXT, lexicon: LEX }), env({ OPENAI_API_KEY: "" }));
  assert.equal(r.status, 503);
});
