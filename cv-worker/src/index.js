/**
 * Serveur du Radar Stages (Cloudflare Worker). Deux services, appelés par le tableau de bord à la demande de l'élève :
 *
 *   POST /cv        texte d'un CV (extrait dans le navigateur) -> fiche de profil structurée (typologie, compétences...)
 *   POST /contacts  entreprise / entité / équipe d'une offre -> personnes à contacter, trouvées par un moteur de
 *                   recherche (profils LinkedIn publics indexés, via Tavily) et décrites par un petit modèle
 *
 * Rien n'est stocké ni journalisé. Garde-fous : origine du site obligatoire, limite de débit par visiteur, entrées
 * plafonnées, requêtes de recherche construites ici (impossible de s'en servir pour chercher autre chose), schémas
 * JSON stricts. Les clés sont des secrets Cloudflare (OPENAI_API_KEY, TAVILY_API_KEY), jamais dans le code.
 */

const MAX_CV_CHARS = 15000;
const ID = /^[a-z_]{1,24}$/;

// ------------------------------------------------------------------ utilitaires
function cleanIds(list) {
  return Array.isArray(list) ? [...new Set(list.filter((x) => typeof x === "string" && ID.test(x)))].slice(0, 80) : [];
}
// texte libre inséré dans une requête de recherche : sans guillemets ni opérateurs, longueur bornée
function cleanTerm(s, max = 80) {
  return String(s || "").replace(/["'()\[\]{}<>|:*]/g, " ").replace(/\s+/g, " ").trim().slice(0, max);
}

async function openai(env, schemaName, schema, system, user, maxTokens = 1500) {
  const call = (model) => {
    const body = {
      model,
      messages: [{ role: "system", content: system }, { role: "user", content: user }],
      response_format: { type: "json_schema", json_schema: { name: schemaName, strict: true, schema } },
      max_completion_tokens: maxTokens,
    };
    if (model.startsWith("gpt-5")) body.reasoning_effort = "minimal";
    return fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  };
  let model = env.MODEL || "gpt-5-nano";
  let r = await call(model);
  if (r.status === 404 && env.FALLBACK_MODEL) { model = env.FALLBACK_MODEL; r = await call(model); }
  if (!r.ok) throw new Error("Le service d'analyse ne répond pas, réessaie plus tard.");
  const choice = (await r.json()).choices?.[0];
  const msg = choice?.message;
  if (choice?.finish_reason === "length") throw new Error("Réponse trop longue, réessaie.");
  if (!msg?.content || msg.refusal) throw new Error("Analyse impossible.");
  return { result: JSON.parse(msg.content), model };
}

// ------------------------------------------------------------------ /cv
const CV_SYSTEM = `Tu lis le CV d'un·e élève ingénieur·e en science des données (Institut Agro Rennes-Angers) qui cherche
un stage de fin d'études. Extrais uniquement ce que le CV atteste (expériences, projets, formation, compétences
listées) ; n'invente rien et ne déduis pas une compétence d'un simple centre d'intérêt.

- typology : le profil en quelques mots (ex. « Data scientist orienté industrie », « Biostatisticien agro »).
- summary : une phrase de 25 mots au plus décrivant le profil et ce qu'il vise.
- skills : compétences attestées, uniquement parmi les identifiants fournis.
- domains : domaines où l'élève a une expérience ou vise explicitement à travailler, avec leur force :
  "forte" (stage ou projet significatif, ou objectif affiché), "moyenne" (cours, projet court), "faible" (mention).
  Un loisir sportif ne fait pas un domaine "forte" sauf si le CV vise explicitement ce secteur.
- target_roles : métiers visés, du plus au moins probable, d'après le titre du CV, le profil et les expériences.
- languages : langues parlées autres que le français et l'anglais, parmi les identifiants fournis.
- experience_months : nombre total de mois de stages ou d'emplois liés à la donnée ou à la recherche.`;

function cvSchema(lex) {
  const arr = (e, n) => ({ type: "array", items: { type: "string", enum: e }, maxItems: n });
  const props = {
    typology: { type: "string" },
    summary: { type: "string" },
    skills: arr(lex.skills, 30),
    domains: {
      type: "array",
      items: {
        type: "object",
        properties: { id: { type: "string", enum: lex.domains }, strength: { type: "string", enum: ["forte", "moyenne", "faible"] } },
        required: ["id", "strength"], additionalProperties: false,
      },
      maxItems: 8,
    },
    target_roles: arr(lex.roles, 4),
    languages: arr(lex.languages, 4),
    experience_months: { type: "integer", minimum: 0, maximum: 240 },
  };
  return { type: "object", properties: props, required: Object.keys(props), additionalProperties: false };
}

async function analyzeCv(payload, env) {
  const text = String(payload.text || "").slice(0, MAX_CV_CHARS).trim();
  if (text.length < 200) return [{ error: "Le CV ne contient pas assez de texte." }, 400];
  const lex = {
    skills: cleanIds(payload.lexicon?.skills), domains: cleanIds(payload.lexicon?.domains),
    roles: cleanIds(payload.lexicon?.roles), languages: cleanIds(payload.lexicon?.languages),
  };
  if (!lex.skills.length || !lex.domains.length || !lex.roles.length) return [{ error: "Dictionnaire manquant." }, 400];
  if (!lex.languages.length) lex.languages = ["aucune"];
  const { result, model } = await openai(env, "profil_cv", cvSchema(lex), CV_SYSTEM, `CV :\n${text}`);
  return [{ profile: result, model }, 200];
}

// ------------------------------------------------------------------ /contacts
const SCHOOLS_HOME = ["Institut Agro Rennes-Angers", "Agrocampus Ouest", "Institut Agro"];
const SCHOOLS_AGRO = ["AgroParisTech", "Institut Agro Montpellier", "Oniris", "ENSAT", "Bordeaux Sciences Agro", "VetAgro Sup", "ISARA", "ESA"];

const CONTACTS_SYSTEM = `On te donne des résultats de recherche web (titre et extrait de profils LinkedIn publics) et une offre
de stage. Pour chaque résultat qui décrit une personne, remplis la fiche d'après le titre et l'extrait uniquement,
sans rien inventer (valeur "inconnu" ou false quand l'information n'y est pas).

- name : prénom et nom tels qu'écrits dans le titre.
- headline : le poste actuel en quelques mots.
- works_at_target : vrai si la personne travaille ACTUELLEMENT dans l'entreprise de l'offre (ou le groupe).
- in_entity : vrai si elle travaille dans l'entité / la filiale / le site précisé pour l'offre.
- school : "home" si elle a étudié à l'Institut Agro Rennes-Angers, Agrocampus Ouest ou l'Institut Agro (ENSAR,
  INH Angers compris) ; "agro" pour une autre école d'agronomie ou vétérinaire (AgroParisTech, Montpellier SupAgro,
  Oniris, ENSAT, Bordeaux Sciences Agro, VetAgro Sup, ISARA, ESA...) ; "none" sinon ou si inconnu.
- data_role : vrai si son poste relève de la data, de la statistique ou du machine learning.
- same_team : vrai si son poste correspond à l'équipe ou au service de l'offre.
- decision : vrai si elle recrute (RH, talent acquisition, relations écoles) ou manage une équipe data.
- recent_grad : "oui" si diplômée il y a moins de 10 ans d'après l'extrait, "non", ou "inconnu".`;

function contactsSchema(urls) {
  const props = {
    url: { type: "string", enum: urls },
    name: { type: "string" },
    headline: { type: "string" },
    works_at_target: { type: "boolean" },
    in_entity: { type: "boolean" },
    school: { type: "string", enum: ["home", "agro", "none"] },
    data_role: { type: "boolean" },
    same_team: { type: "boolean" },
    decision: { type: "boolean" },
    recent_grad: { type: "string", enum: ["oui", "non", "inconnu"] },
  };
  return {
    type: "object",
    properties: { people: { type: "array", maxItems: 25, items: { type: "object", properties: props, required: Object.keys(props), additionalProperties: false } } },
    required: ["people"], additionalProperties: false,
  };
}

async function tavily(env, query) {
  const r = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.TAVILY_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ query, include_domains: ["linkedin.com"], max_results: 10, search_depth: "basic", country: "france" }),
  });
  if (!r.ok) throw new Error(r.status === 432 || r.status === 429 ? "Quota de recherche épuisé ce mois-ci." : "Le moteur de recherche ne répond pas.");
  return (await r.json()).results || [];
}

async function findContacts(payload, env) {
  if (!env.TAVILY_API_KEY) return [{ error: "Recherche de contacts non configurée." }, 503];
  const company = cleanTerm(payload.company), entity = cleanTerm(payload.entity), team = cleanTerm(payload.team, 60);
  const title = cleanTerm(payload.title, 140);
  if (!company) return [{ error: "Entreprise manquante." }, 400];
  const org = entity || company;
  // requêtes en langage naturel (moteur sémantique), limitées aux profils LinkedIn publics par include_domains
  const queries = [
    `${org} ingénieur agronome ancien élève ${SCHOOLS_HOME[1]} ${SCHOOLS_HOME[2]} ${SCHOOLS_AGRO[0]}`,
    `${org} data scientist data analyst France`,
    team ? `${org} ${team}` : `${org} talent acquisition chargée de recrutement stages France`,
  ];
  const seen = new Map();
  for (const results of await Promise.all(queries.map((q) => tavily(env, q)))) {
    for (const r of results) {
      const url = String(r.url || "").split("?")[0];
      if (/^https:\/\/([a-z]{2,3}\.)?linkedin\.com\/in\/[^/]+\/?$/i.test(url) && !seen.has(url)) {
        seen.set(url, { url, title: String(r.title || "").slice(0, 200), snippet: String(r.content || "").slice(0, 500) });
      }
    }
  }
  const found = [...seen.values()].slice(0, 25);
  if (!found.length) return [{ people: [], searched: queries.length }, 200];
  const user = `Offre : « ${title} » chez ${company}${entity ? ` (entité : ${entity})` : ""}${team ? ` ; équipe : ${team}` : ""}.\n\nRésultats :\n` +
    found.map((f, i) => `${i + 1}. ${f.url}\n   ${f.title}\n   ${f.snippet}`).join("\n");
  const { result, model } = await openai(env, "contacts", contactsSchema(found.map((f) => f.url)), CONTACTS_SYSTEM, user, 3000);
  // on ne garde que les personnes de l'entreprise, ou les anciens d'écoles agro qui y sont passés
  const people = result.people.filter((p) => p.works_at_target || p.school !== "none");
  return [{ people, searched: queries.length, model }, 200];
}

// ------------------------------------------------------------------ routage
export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
    const cors = allowed.includes(origin) ? {
      "Access-Control-Allow-Origin": origin, "Vary": "Origin",
      "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
    } : null;
    const reply = (obj, status = 200) => new Response(JSON.stringify(obj), {
      status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...(cors || {}) },
    });

    if (!cors) return reply({ error: "Origine non autorisée." }, 403);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST") return reply({ error: "Méthode non autorisée." }, 405);
    if (!env.OPENAI_API_KEY) return reply({ error: "Service non configuré." }, 503);

    const path = new URL(request.url).pathname.replace(/\/+$/, "") || "/cv";
    const route = { "/cv": analyzeCv, "/contacts": findContacts }[path];
    if (!route) return reply({ error: "Service inconnu." }, 404);

    if (env.RATE_LIMITER) {
      const ip = request.headers.get("CF-Connecting-IP") || "inconnu";
      const { success } = await env.RATE_LIMITER.limit({ key: `${path}:${ip}` });
      if (!success) return reply({ error: "Trop de demandes d'affilée : réessaie dans une minute." }, 429);
    }
    if (Number(request.headers.get("Content-Length") || 0) > 200000) return reply({ error: "Requête trop volumineuse." }, 413);

    let payload;
    try { payload = await request.json(); } catch { return reply({ error: "Requête illisible." }, 400); }
    try {
      const [body, status] = await route(payload, env);
      return reply(body, status);
    } catch (e) {
      return reply({ error: e.message || "Erreur du service." }, 502);
    }
  },
};
