// Tests du Worker sans appeler OpenAI ni Tavily (fetch simulé).   npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

const ORIGIN = "https://pierridotite.github.io";
const LEX = { skills: ["python", "r", "sql"], roles: ["ds", "stat"], sectors: ["data", "agro", "sport"],
  languages: ["es"], regions: ["Bretagne", "Île-de-France"] };
const CV_TEXT = "Élève ingénieur en data science, maintenance prédictive et capteurs. ".repeat(10);
const PROFILE = { typology: "Data scientist orienté industrie", summary: "ML appliqué aux capteurs.", skills: ["python"],
  keywords: [{ term: "maintenance prédictive", synonyms: ["predictive maintenance"] }], sectors: ["data"],
  target_roles: ["ds"], regions: [], languages: [] };

const env = (extra = {}) => ({ OPENAI_API_KEY: "test", TAVILY_API_KEY: "t", ALLOWED_ORIGINS: ORIGIN,
  MODEL: "gpt-5-nano", FALLBACK_MODEL: "gpt-4.1-nano", ...extra });
const post = (path, body, origin = ORIGIN) => new Request(`https://w.example${path}`, { method: "POST",
  headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify(body) });

function mock({ missingModel, results } = {}) {
  const calls = { openai: [], tavily: [] };
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    if (String(url).includes("tavily")) {
      calls.tavily.push(body);
      return new Response(JSON.stringify({ results: results ?? [
        { url: "https://fr.linkedin.com/in/camille-m?trk=x", title: "Camille Martin - Data Scientist - Danone | LinkedIn",
          content: "Expérience : Danone · Formation : Agrocampus Ouest" },
        { url: "https://fr.linkedin.com/in/alex-r", title: "Alex Roux – Talent Acquisition Partner – Danone | LinkedIn", content: "Danone" },
        { url: "https://fr.linkedin.com/in/sam-x", title: "Sam X - Consultant - Autre société | LinkedIn", content: "rien à voir" },
        { url: "https://www.linkedin.com/company/danone", title: "Danone | LinkedIn", content: "page entreprise" },
        { url: "https://fr.linkedin.com/in/camille-m", title: "doublon", content: "" },
      ] }), { status: 200 });
    }
    calls.openai.push(body);
    if (body.model === missingModel) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(PROFILE) } }] }), { status: 200 });
  };
  return calls;
}

test("refuse une origine inconnue et répond au préflight du site", async () => {
  mock();
  assert.equal((await worker.fetch(post("/cv", {}, "https://evil.example"), env())).status, 403);
  const pre = await worker.fetch(new Request("https://w.example/cv", { method: "OPTIONS", headers: { Origin: ORIGIN } }), env());
  assert.equal(pre.status, 204);
});

test("/cv : filtres du profil avec un schéma strict limité au dictionnaire", async () => {
  const calls = mock();
  const r = await worker.fetch(post("/cv", { text: CV_TEXT, lexicon: LEX }), env());
  assert.equal(r.status, 200);
  assert.deepEqual((await r.json()).profile, PROFILE);
  const schema = calls.openai[0].response_format.json_schema;
  assert.equal(schema.strict, true);
  assert.deepEqual(schema.schema.properties.sectors.items.enum, LEX.sectors);
  assert.deepEqual(schema.schema.properties.regions.items.enum, LEX.regions);
  assert.equal(schema.schema.properties.keywords.maxItems, 12);
});

test("/cv : CV trop court refusé, CV trop long tronqué, identifiants invalides ignorés", async () => {
  const calls = mock();
  assert.equal((await worker.fetch(post("/cv", { text: "court", lexicon: LEX }), env())).status, 400);
  await worker.fetch(post("/cv", { text: "x".repeat(50000), lexicon: { ...LEX, skills: ["python", "<script>", 42] } }), env());
  assert.ok(calls.openai[0].messages[1].content.length <= 15010);
  assert.deepEqual(calls.openai[0].response_format.json_schema.schema.properties.skills.items.enum, ["python"]);
});

test("/cv : modèle de secours si le premier est indisponible", async () => {
  const calls = mock({ missingModel: "gpt-5-nano" });
  const r = await worker.fetch(post("/cv", { text: CV_TEXT, lexicon: LEX }), env());
  assert.equal((await r.json()).model, "gpt-4.1-nano");
  assert.equal(calls.openai.length, 2);
});

test("/contacts : sans aucun appel au modèle, profils lus dans les résultats", async () => {
  const calls = mock();
  const r = await worker.fetch(post("/contacts", { company: "Groupe Danone", entity: "" }), env());
  assert.equal(r.status, 200);
  const { people } = await r.json();
  assert.equal(calls.openai.length, 0, "la recherche de contacts ne doit pas utiliser l'IA");
  assert.equal(calls.tavily.length, 3);
  assert.ok(calls.tavily.every((b) => b.include_domains[0] === "linkedin.com"));
  const byName = Object.fromEntries(people.map((p) => [p.name, p]));
  assert.deepEqual(Object.keys(byName).sort(), ["Alex Roux", "Camille Martin"]);  // hors entreprise et doublon écartés
  assert.equal(byName["Camille Martin"].school, "home");
  assert.equal(byName["Camille Martin"].data_role, true);
  assert.equal(byName["Camille Martin"].url, "https://fr.linkedin.com/in/camille-m");
  assert.equal(byName["Alex Roux"].decision, true);
});

test("/contacts : non configuré sans clé de recherche ; route inconnue refusée ; limite de débit", async () => {
  mock();
  assert.equal((await worker.fetch(post("/contacts", { company: "Danone" }), env({ TAVILY_API_KEY: "" }))).status, 503);
  assert.equal((await worker.fetch(post("/autre", {}), env())).status, 404);
  const limited = env({ RATE_LIMITER: { limit: async () => ({ success: false }) } });
  assert.equal((await worker.fetch(post("/cv", { text: CV_TEXT, lexicon: LEX }), limited)).status, 429);
});

test("/contacts : les requêtes de recherche sont nettoyées (pas d'opérateurs injectés)", async () => {
  const calls = mock();
  await worker.fetch(post("/contacts", { company: 'Danone" OR site:evil.com' }), env());
  assert.ok(calls.tavily.every((b) => !/["():]/.test(b.query)));
});
