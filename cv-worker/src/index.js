/**
 * Serveur du Radar Stages (Cloudflare Worker), appelé par le tableau de bord à la demande de l'élève :
 *
 *   POST /cv        texte d'un CV (extrait dans le navigateur) -> filtres du profil, extraits par un petit modèle
 *                   (compétences, mots-clés précis, secteurs, métiers visés, régions, langues). Seul usage de l'IA.
 *   POST /contacts  entreprise / entité d'une offre -> personnes à contacter : profils LinkedIn publics trouvés par
 *                   le moteur Tavily, décrits SANS IA (nom, poste et école lus dans le titre et l'extrait).
 *
 * Rien n'est stocké ni journalisé. Garde-fous : origine du site obligatoire, limite de débit par visiteur, entrées
 * plafonnées, requêtes de recherche construites ici, schéma JSON strict pour le modèle.
 * Clés : secrets Cloudflare OPENAI_API_KEY (/cv) et TAVILY_API_KEY (/contacts), jamais dans le code.
 */

const MAX_CV_CHARS = 15000;
const ID = /^[a-z_]{1,24}$/;

// ------------------------------------------------------------------ utilitaires
function cleanIds(list, max = 80) {
  return Array.isArray(list) ? [...new Set(list.filter((x) => typeof x === "string" && ID.test(x)))].slice(0, max) : [];
}
function cleanLabels(list, max = 20) {
  return Array.isArray(list)
    ? [...new Set(list.filter((x) => typeof x === "string").map((x) => x.trim().slice(0, 40)).filter(Boolean))].slice(0, max)
    : [];
}
// texte libre inséré dans une requête de recherche : sans guillemets ni opérateurs, longueur bornée
function cleanTerm(s, max = 80) {
  return String(s || "").replace(/["'()\[\]{}<>|:*]/g, " ").replace(/\s+/g, " ").trim().slice(0, max);
}
const fold = (s) => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

// ------------------------------------------------------------------ /cv : filtres du profil (seul appel au modèle)
const CV_SYSTEM = `Tu lis le CV d'un·e élève ingénieur·e en science des données (Institut Agro Rennes-Angers) qui cherche
un stage de fin d'études, pour en tirer des FILTRES de recherche d'offres. N'utilise que ce que le CV atteste ou
affiche comme objectif ; n'invente rien.

- typology : le profil en quelques mots (ex. « Data scientist orienté industrie », « Biostatisticienne sport-santé »).
- summary : une phrase de 20 mots au plus sur ce que l'élève sait faire et vise.
- skills : compétences techniques attestées, uniquement parmi les identifiants fournis.
- keywords : 5 à 12 SUJETS MÉTIER ou domaines d'application précis et distinctifs de ce profil, de 1 à 3 mots, tels
  qu'on les lirait dans une offre de stage (ex. « maintenance prédictive », « séries temporelles », « capteurs »,
  « performance sportive », « sélection variétale », « analyse sensorielle », « nutrition animale », « RAG »), chacun
  avec 0 à 3 synonymes ou traductions courtes (français / anglais). Ne répète pas skills : ni outils, ni langages, ni
  méthodes génériques (« machine learning », « statistiques », « ANOVA », « ACP », « régression », « nettoyage de
  données »). Pas de mots trop larges (« data », « analyse », « production », « automatisation », « qualité »,
  « dashboard », « pipeline », « tests »), pas de loisirs sans lien avec le métier visé.
- sectors : 1 à 3 secteurs, parmi les identifiants fournis, où l'élève a une vraie expérience (stage, projet majeur)
  ou vise explicitement un stage, du plus au moins important. « data » seulement si le profil vise des entreprises
  tech / data sans secteur d'application marqué.
- target_roles : 1 ou 2 métiers principaux visés, du plus probable au moins probable.
- regions : régions françaises où l'élève dit chercher (mobilité, préférence géographique explicite), parmi celles
  fournies ; une simple adresse ou l'école ne compte pas ; liste vide s'il n'y a pas d'indication.
- languages : langues parlées autres que le français et l'anglais, parmi les identifiants fournis.`;

function cvSchema(lex) {
  const arr = (e, n) => ({ type: "array", items: { type: "string", enum: e }, maxItems: n });
  const props = {
    typology: { type: "string" },
    summary: { type: "string" },
    skills: arr(lex.skills, 30),
    keywords: {
      type: "array", maxItems: 12,
      items: {
        type: "object",
        properties: { term: { type: "string" }, synonyms: { type: "array", items: { type: "string" }, maxItems: 3 } },
        required: ["term", "synonyms"], additionalProperties: false,
      },
    },
    sectors: arr(lex.sectors, 3),
    target_roles: arr(lex.roles, 2),
    regions: arr(lex.regions, 5),
    languages: arr(lex.languages, 4),
  };
  return { type: "object", properties: props, required: Object.keys(props), additionalProperties: false };
}

async function openai(env, schema, text) {
  const call = (model) => {
    const body = {
      model,
      messages: [{ role: "system", content: CV_SYSTEM }, { role: "user", content: `CV :\n${text}` }],
      response_format: { type: "json_schema", json_schema: { name: "filtres_cv", strict: true, schema } },
      max_completion_tokens: 2500,
    };
    if (model.startsWith("gpt-5")) body.reasoning_effort = "low";
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
  if (choice?.finish_reason === "length") throw new Error("Réponse trop longue, réessaie.");
  if (!choice?.message?.content || choice.message.refusal) throw new Error("Analyse impossible pour ce CV.");
  return { result: JSON.parse(choice.message.content), model };
}

async function analyzeCv(payload, env) {
  if (!env.OPENAI_API_KEY) return [{ error: "Analyse de CV non configurée." }, 503];
  const text = String(payload.text || "").slice(0, MAX_CV_CHARS).trim();
  if (text.length < 200) return [{ error: "Le CV ne contient pas assez de texte." }, 400];
  const lex = {
    skills: cleanIds(payload.lexicon?.skills), roles: cleanIds(payload.lexicon?.roles),
    sectors: cleanIds(payload.lexicon?.sectors, 12), languages: cleanIds(payload.lexicon?.languages),
    regions: cleanLabels(payload.lexicon?.regions),
  };
  if (!lex.skills.length || !lex.roles.length || !lex.sectors.length) return [{ error: "Dictionnaire manquant." }, 400];
  if (!lex.languages.length) lex.languages = ["aucune"];
  if (!lex.regions.length) lex.regions = ["aucune"];
  const { result, model } = await openai(env, cvSchema(lex), text);
  return [{ profile: result, model }, 200];
}

// ------------------------------------------------------------------ /contacts : recherche + lecture sans IA
const HOME_SCHOOL = /agrocampus|institut agro rennes|agro rennes|rennes-angers|ensa ?rennes|inh angers|institut national d.horticulture/i;
const AGRO_SCHOOL = /agroparistech|institut agro|montpellier supagro|oniris|ensat|bordeaux sciences agro|vetagro|isara|esa angers|purpan|ensaia|agrosup|isa lille|junia/i;
const DATA_ROLE = /\bdata\b|donn[ée]es|machine learning|statisti|analyst|analyste|scientist|\bbi\b|\bia\b|\bai\b|mod[ée]lisation|biostat/i;
const RECRUITER = /talent|recrut|recruit|\brh\b|\bhr\b|campus|relations? [ée]coles|people partner|human resources|ressources humaines/i;
const STOP = new Set(["groupe", "group", "the", "and", "les", "des", "sas", "france", "company", "societe", "holding", "international"]);
const MANAGER = /head of|manager|directeur|directrice|director|responsable|chief|\blead\b|\bvp\b/i;

function readProfile(r, company, entity) {
  const title = String(r.title || "").replace(/\s*[|–-]\s*LinkedIn.*$/i, "").trim();
  const [name, ...rest] = title.split(/\s+[-–|]\s+/);
  const headline = rest.join(" · ").slice(0, 120);
  const text = `${title} ${String(r.content || "")}`;
  // l'entreprise est reconnue si tous ses mots significatifs figurent dans le titre ou l'extrait
  // ("Groupe Casino" -> "casino" ; "Crédit Agricole" -> "credit" + "agricole")
  const words = fold(text);
  const inText = (term) => {
    const toks = fold(term).split(/[^a-z0-9&]+/).filter((t) => t.length >= 3 && !STOP.has(t));
    // les mots ne contiennent que [a-z0-9&] : aucun caractère spécial de regex à échapper
    return toks.length > 0 && toks.every((t) => new RegExp(`(^|[^a-z0-9])${t}([^a-z0-9]|$)`).test(words));
  };
  return {
    url: String(r.url || "").split("?")[0], name: (name || "").slice(0, 60), headline,
    works_at_target: inText(company) || inText(entity),
    in_entity: inText(entity),
    school: HOME_SCHOOL.test(text) ? "home" : AGRO_SCHOOL.test(text) ? "agro" : "none",
    data_role: DATA_ROLE.test(headline || text),
    decision: RECRUITER.test(headline) || (MANAGER.test(headline) && DATA_ROLE.test(headline)),
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
  const company = cleanTerm(payload.company), entity = cleanTerm(payload.entity);
  if (!company) return [{ error: "Entreprise manquante." }, 400];
  const org = entity || company;
  // requêtes en langage naturel (moteur sémantique), limitées aux profils LinkedIn publics par include_domains
  const queries = [
    `${org} ingénieur agronome Agrocampus Ouest Institut Agro AgroParisTech`,
    `${org} data scientist data analyst France`,
    `${org} talent acquisition chargée de recrutement stages France`,
  ];
  const seen = new Map();
  for (const results of await Promise.all(queries.map((q) => tavily(env, q)))) {
    for (const r of results) {
      const url = String(r.url || "").split("?")[0];
      if (/^https:\/\/([a-z]{2,3}\.)?linkedin\.com\/in\/[^/]+\/?$/i.test(url) && !seen.has(url)) {
        seen.set(url, readProfile({ ...r, url }, company, entity));
      }
    }
  }
  // on garde les personnes de l'entreprise, et les anciens d'écoles agro qui y sont liés
  const people = [...seen.values()].filter((p) => p.name && (p.works_at_target || p.school !== "none")).slice(0, 25);
  return [{ people, searched: queries.length }, 200];
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
