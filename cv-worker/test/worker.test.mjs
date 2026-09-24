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
        // formats réels des extraits : en-tête « # Nom Poste Lieu, FR  N connections », puis sections
        { url: "https://fr.linkedin.com/in/camille-m?trk=x", title: "Camille Martin - Data Analyst On Premise - Red Bull | LinkedIn",
          content: "# Camille Martin Data Analyst On Premise chez Red Bull Paris, Île-de-France, France, FR   500 connections, 900 followers  ## About N/A  ## Experience ### Red Bull   Red Bull   N/A - Present  ## Education ### Agrocampus Ouest" },
        { url: "https://fr.linkedin.com/in/alex-r/en", title: "Alex Roux – Talent Acquisition Partner – Red Bull | LinkedIn",
          content: "Talent Acquisition Partner · Expérience : Red Bull · Lieu : Lyon" },
        { url: "https://at.linkedin.com/in/max-h", title: "Max Huber - Head of Data - Red Bull | LinkedIn",
          content: "# Max Huber Head of Data Red Bull Salzburg, Salzburg, Austria, AT   500 connections  ## Experience ### Head of Data   Red Bull" },
        { url: "https://fr.linkedin.com/in/ancien", title: "Léa Petit - Consultante - Oresys | LinkedIn",
          content: "# Léa Petit Consultante Oresys Paris, Île-de-France, France, FR   300 connections  ## Experience ### Oresys  ## Education ### Agrocampus Ouest  ## People Also Viewed - Jo Bo (Red Bull)" },
        { url: "https://fr.linkedin.com/in/sam-x", title: "Sam X | LinkedIn", content: "## People Also Viewed - Anthony Pilet (Red Bull)" },
        { url: "https://fr.linkedin.com/in/tom-ex", title: "Tom Ex - Ex Red Bull Data Analyst | LinkedIn",
          content: "# Tom Ex\nEx Red Bull Data Analyst\nParis, Île-de-France, France, FR\n500 connections" },
        { url: "https://fr.linkedin.com/in/fan", title: "Jo Fan - New York Red Bulls | LinkedIn", content: "# Jo Fan Supporter New York, United States, US   20 connections" },
        { url: "https://www.linkedin.com/company/redbull", title: "Red Bull | LinkedIn", content: "page entreprise" },
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

test("/contacts : entonnoir sans IA, seules les personnes de l'entreprise, critères liés à l'offre", async () => {
  const calls = mock();
  const r = await worker.fetch(post("/contacts", { company: "Red Bull", entity: "",
    title: "Stage Data Specialist Hors-Domicile (H/F)", city: "Paris" }), env());
  assert.equal(r.status, 200);
  const { people } = await r.json();
  assert.equal(calls.openai.length, 0, "la recherche de contacts ne doit pas utiliser l'IA");
  assert.ok(calls.tavily.every((b) => b.include_domains[0] === "linkedin.com/in"), "profils LinkedIn uniquement");
  assert.match(calls.tavily[0].query, /Red Bull .*specialist hors domicile Paris/i, "1re requête : même poste, même ville");
  assert.ok(!/stage|h\/f/i.test(calls.tavily[0].query), "contrat et durée retirés de l'intitulé");
  assert.equal(calls.tavily.length, 4, "peu de résultats dans l'entreprise : recherche élargie au pays");
  assert.match(calls.tavily[1].query, /France$/);
  const byName = Object.fromEntries(people.map((p) => [p.name, p]));
  // ancienne de l'école ailleurs, ex-salarié, entreprise citée seulement dans « People also viewed », homonyme (Red Bulls),
  // page entreprise et doublon écartés
  assert.deepEqual(Object.keys(byName).sort(), ["Alex Roux", "Camille Martin", "Max Huber"]);
  const c = byName["Camille Martin"];
  // « On Premise » = « Hors-Domicile » : même équipe ; data analyst ~ data specialist : même métier
  assert.deepEqual([c.same_team, c.same_role, c.school, c.location, c.country], [true, true, "home", "Paris, Île-de-France, France", "FR"]);
  assert.deepEqual([byName["Alex Roux"].recruiter, byName["Alex Roux"].location], [true, "Lyon"]);
  assert.equal(byName["Alex Roux"].url, "https://fr.linkedin.com/in/alex-r");
  assert.equal(byName["Max Huber"].manager, true);
  assert.deepEqual([byName["Max Huber"].location, byName["Max Huber"].country], ["Salzburg, Salzburg, Austria", "AT"]);
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
  await worker.fetch(post("/contacts", { company: 'Danone" OR site:evil.com', title: "Stage (data) : [x]", city: "Paris*" }), env());
  assert.ok(calls.tavily.every((b) => !/["():]/.test(b.query)));
});
