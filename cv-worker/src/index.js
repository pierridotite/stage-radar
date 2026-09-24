/**
 * Serveur du Radar Stages (Cloudflare Worker), appelé par le tableau de bord à la demande de l'élève :
 *
 *   POST /cv        texte d'un CV (extrait dans le navigateur) -> filtres du profil, extraits par un petit modèle
 *                   (compétences, mots-clés précis, secteurs, métiers visés, régions, langues). Seul usage de l'IA.
 *   POST /contacts  offre (entreprise, entité, intitulé, ville) -> personnes de l'entreprise à contacter : profils
 *                   LinkedIn publics trouvés par le moteur Tavily en entonnoir (même poste au même endroit, puis
 *                   dans le pays, manager de l'équipe, recrutement), décrits SANS IA. Cache partagé 7 jours par offre.
 *
 * Rien n'est journalisé ; aucun CV n'est stocké. Garde-fous : origine du site obligatoire, limite de débit par visiteur, entrées
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

// ------------------------------------------------------------------ /contacts : recherche en entonnoir, lecture sans IA
const HOME_SCHOOL = /agrocampus|institut agro rennes|institut agro \| rennes|agro rennes|rennes-angers|ensa ?rennes|\bensar\b|inh angers|institut national d.horticulture/i;
const AGRO_SCHOOL = /agroparistech|institut agro|montpellier supagro|oniris|ensat|bordeaux sciences agro|vetagro|isara|esa angers|purpan|ensaia|agrosup|isa lille|junia/i;
const DATA_ROLE = /\bdata\b|donnees|machine learning|statisti|analyst|analyste|scientist|\bbi\b|\bia\b|\bai\b|modelisation|biostat/;
const RECRUITER = /talent|recrut|recruit|\brh\b|\bhr\b|campus|relations? ecoles|people partner|human resources|ressources humaines/;
const MANAGER = /head of|manager|directeur|directrice|director|responsable|chief|\blead\b|\bvp\b|chef d/;
const STUDENT = /student|etudiant|stagiaire|\bintern\b|alternant|apprenti/;
const STOP = new Set(["groupe", "group", "the", "and", "les", "des", "sas", "france", "company", "societe", "holding", "international"]);
// familles de métiers, pour comparer l'intitulé de l'offre et le poste de la personne
const ROLE_FAMILIES = {
  ds: /scientist|science des donnees|data science|machine learning|\bml\b|deep learning|intelligence artificielle|\bia\b|\bai\b|\bnlp\b|computer vision/,
  da: /analyst|analyste|analytics|business intelligence|\bbi\b|insights?|data specialist|specialiste data|reporting|dataviz/,
  de: /data engineer|ingenieure? (des )?donnees|ingenieure? data|analytics engineer|data architect|data platform|big data/,
  stat: /statisti|biostat|biometri/,
};
// mots d'un intitulé qui ne disent rien de l'équipe (contrat, durée, métier générique)
const TITLE_NOISE = /\([^)]*\)|\b(h|f|m|x|w|d)\s*\/\s*(h|f|m|x|w|d)(\s*\/\s*(h|f|m|x|w|d))?\b|\b(stage|stagiaire|internship|intern|alternance|apprenti\w*|fin d.etudes|end of studies|pfe|\d+\s*(mois|months)|20\d\d|bac\s*\+\s*\d|cs\s*\d+|all gender|janvier|fevrier|january|february)\b|\b\d[\d-]*\b/g;
const ROLE_WORDS = new Set(["data", "donnees", "scientist", "science", "sciences", "analyst", "analyste", "analyse", "analytics",
  "engineer", "ingenieur", "ingenieure", "specialist", "specialiste", "charge", "chargee", "assistant", "assistante", "junior",
  "business", "intelligence", "artificielle", "machine", "learning", "deep", "statisticien", "statisticienne", "statistique",
  "statistiques", "modelisation", "projet", "mission", "poste", "consultant", "consultante", "developpement", "avec", "pour",
  "dans", "des", "les", "une", "sur", "from", "with", "and", "the", "vers", "entre", "chez"]);

const families = (t) => Object.keys(ROLE_FAMILIES).filter((f) => ROLE_FAMILIES[f].test(t));

function jobOf(title) {
  const phrase = fold(title).replace(TITLE_NOISE, " ").replace(/[^a-z0-9&' ]+/g, " ").replace(/\s+/g, " ").trim();
  const team = [...new Set(phrase.split(" ").filter((w) => w.length >= 4 && !ROLE_WORDS.has(w)))].slice(0, 6);
  return { phrase: cleanTerm(phrase, 60), team, families: families(fold(title)) };
}

// équipes dites autrement sur les profils (souvent en anglais) : on ajoute le terme de l'offre au poste de la personne
const TEAM_SYNONYMS = [
  ["hors domicile", /on.?premise|on.?trade|\bchr\b|\bchd\b|out.of.home|restauration|foodservice/],
  ["grande distribution", /off.?premise|off.?trade|\bgms\b|retail|grande distribution|hypermarch|supermarch/],
  ["supply chain", /supply|logisti|approvisionnement|planning/],
  ["marketing", /marketing|brand|marque/],
  ["finance", /financ|controll|controle de gestion|comptab/],
  ["commercial", /sales|vente|commercial|account/],
  ["ressources humaines", /\bhr\b|\brh\b|human resources|people/],
  ["recherche developpement", /r&d|\brnd\b|research|recherche|innovation/],
  ["qualite", /quality|qualite/],
  ["production", /production|manufacturing|usine|plant|site manager/],
];

// lieu lu dans l'en-tête du profil, sur plusieurs lignes (« # Nom / Poste / Paris, Île-de-France, France, FR /
// 500 connections ») ou sur une seule quand l'extrait est aplati
function placeFrom(header) {
  const lines = header.split("\n").map((x) => x.trim()).filter(Boolean);
  const line = lines.find((x) => /,\s*[A-Z]{2}$/.test(x));
  if (!line) return { location: "", country: "" };
  const m = line.match(/^(.*),\s*([A-Z]{2})$/);
  if (lines.length > 1) return { location: m[1].trim().slice(0, 70), country: m[2] };
  // extrait aplati : « Poste Ville, Région, Pays » sur une ligne, on isole la ville
  const parts = m[1].split(",").map((x) => x.trim());
  let first = parts[0];
  const area = first.match(/((?:Greater|Région de|Region de|Grand)\s[\w' -]+|[\w' -]+\s(?:Area|Metropolitan Region))$/i);
  first = area ? area[1] : parts.length > 1 ? first.split(/\s+/).slice(-1)[0] : first.split(/\s+/).slice(-3).join(" ");
  return { location: [first, ...parts.slice(1, 3)].join(", ").slice(0, 70), country: m[2] };
}

function readProfile(r, company, entity, job) {
  const title = String(r.title || "").replace(/\s*[|–-]\s*LinkedIn.*$/i, "").trim();
  const [name, ...rest] = title.split(/\s+[-–|]\s+/);
  const content = String(r.content || "");
  // en-tête du profil et poste actuel (1re entrée de « Experience ») ; le reste (activité, « People also viewed »,
  // anciens postes) peut citer d'autres entreprises et n'est pas utilisé pour savoir où la personne travaille
  // (l'en-tête n'est pas toujours en début d'extrait : il peut suivre l'activité de la personne)
  // en-tête = du « # Nom » jusqu'au nombre de relations (au-delà : activité, « People also viewed »...)
  const header = (content.match(/(?:^|[^#\w])#[ \t]+([^#]+)/)?.[1] || "")
    .split(/\d[\d,.+]*\s*(?:connections|relations|followers|abonnés)|\n\s*\n/)[0].replace(name || "", "").trim();
  // 1re expérience : « ### Poste \n Entreprise \n dates » ; « N/A » = champ vide
  const exp = (content.match(/##\s*Experience\s*###([^\n]*\n[^\n#]*)/i)?.[1] || "")
    .replace(/\bN\/A\b/g, " ").replace(/\s+/g, " ").trim().slice(0, 140);
  const legacy = content.match(/(?:exp[ée]rience)\s*:\s*([^·|\n]{2,80})/i)?.[1] || "";   // format « Expérience : X · Lieu : Y »
  let headline = rest.join(" · ").slice(0, 120);
  const place = placeFrom(header);
  if (!place.location) place.location = (content.match(/(?:lieu|location)\s*:\s*([^·|\n]{2,70})/i)?.[1] || "").trim();
  // l'entreprise est reconnue si tous ses mots significatifs figurent dans le texte visé
  // ("Groupe Casino" -> "casino" ; "Crédit Agricole" -> "credit" + "agricole")
  const has = (term, where) => {
    const toks = fold(term).split(/[^a-z0-9&]+/).filter((t) => t.length >= 3 && !STOP.has(t));
    // les mots ne contiennent que [a-z0-9&] : aucun caractère spécial de regex à échapper
    return toks.length > 0 && toks.every((t) => new RegExp(`(^|[^a-z0-9])${t}([^a-z0-9]|$)`).test(fold(where)));
  };
  const current = `${headline} ${header} ${exp} ${legacy}`;
  // « Ex Nestlé », « Former Red Bull », « ancienne de Danone » : plus dans l'entreprise
  const firstTok = fold(company).split(/[^a-z0-9&]+/).find((t) => t.length >= 3 && !STOP.has(t)) || "";
  const former = !!firstTok && new RegExp(`(^|[^a-z])(ex|former|formerly|ancien|ancienne)[\\s-]+(de |chez |at )?${firstTok}`).test(fold(headline));
  const works = !former && (has(company, current) || (!!entity && has(entity, current)));
  // poste réel : titre du profil, sinon 1re expérience (quand le titre ne donne que l'entreprise)
  // titre réduit au nom de l'entreprise : on prend la 2e ligne de l'en-tête ou la 1re expérience, si elles en disent plus
  const headerTitle = header.split("\n").map((x) => x.trim()).filter(Boolean)[0] || "";
  if (!headline || fold(headline).trim() === fold(company).trim()) {
    const informative = (x) => fold(x).split(fold(company)).join(" ").replace(/present|\d{4}|[^a-z]+/g, " ").trim().length > 3;
    headline = [headerTitle, exp].find((x) => x && informative(x))?.slice(0, 120) || headline;
  }
  let h = fold(`${headline} ${exp}`);
  for (const [canon, rx] of TEAM_SYNONYMS) if (rx.test(h)) h += ` ${canon}`;
  const hits = job.team.filter((w) => new RegExp(`(^|[^a-z0-9])${w}`).test(h)).length;
  const sameRole = job.families.length > 0 && families(h).some((f) => job.families.includes(f));
  const sameTeam = (job.team.length > 0 && hits >= Math.min(2, job.team.length)) || (!!entity && has(entity, current));
  const dataRole = DATA_ROLE.test(h);
  const text = `${title} ${content.split(/##\s*People Also Viewed/i)[0]}`;
  return {
    url: String(r.url || "").split("?")[0].replace(/\/(?:[a-z]{2})\/?$/, ""), name: (name || "").slice(0, 60),
    headline, location: place.location, country: place.country,
    works_at_target: works, same_team: sameTeam, same_role: sameRole, data_role: dataRole,
    manager: MANAGER.test(h) && !STUDENT.test(h) && (sameTeam || sameRole || dataRole), recruiter: RECRUITER.test(fold(headline)),
    school: HOME_SCHOOL.test(text) ? "home" : AGRO_SCHOOL.test(text) ? "agro" : "none",
  };
}

async function tavily(env, query) {
  const r = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.TAVILY_API_KEY}`, "Content-Type": "application/json" },
    // limité aux profils (linkedin.com/in) : sans cela, pages d'offres et sites sans rapport dominent les résultats
    body: JSON.stringify({ query, include_domains: ["linkedin.com/in"], max_results: 10, search_depth: "basic" }),
  });
  if (!r.ok) throw new Error(r.status === 432 || r.status === 429 ? "Quota de recherche épuisé ce mois-ci." : "Le moteur de recherche ne répond pas.");
  return (await r.json()).results || [];
}

// cache partagé 7 jours par offre (Cache API de Cloudflare) : deux élèves sur la même offre = une seule recherche
async function cached(key, compute) {
  if (typeof caches === "undefined") return compute();
  const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key)))]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  const req = new Request(`https://contacts-cache.radar/${hash}`);
  const hit = await caches.default.match(req);
  if (hit) return [{ ...(await hit.json()), cached: true }, 200];
  const [body, status] = await compute();
  if (status === 200) {
    await caches.default.put(req, new Response(JSON.stringify(body), { headers: { "Cache-Control": "max-age=604800" } }));
  }
  return [body, status];
}

async function findContacts(payload, env) {
  if (!env.TAVILY_API_KEY) return [{ error: "Recherche de contacts non configurée." }, 503];
  const company = cleanTerm(payload.company), entity = cleanTerm(payload.entity);
  const city = cleanTerm(payload.city, 40);
  const job = jobOf(cleanTerm(payload.title, 160));
  if (!company) return [{ error: "Entreprise manquante." }, 400];
  return cached(JSON.stringify(["v4", company, entity, city, job.phrase]), async () => {
    const seen = new Map();
    const run = async (queries) => {
      for (const results of await Promise.all(queries.map((q) => tavily(env, q)))) {
        for (const r of results) {
          const url = String(r.url || "").split("?")[0].replace(/\/(?:[a-z]{2})\/?$/, "");   // /in/nom/en -> /in/nom
          if (/^https:\/\/([a-z]{2,3}\.)?linkedin\.com\/in\/[^/]+\/?$/i.test(url) && !seen.has(url)) {
            seen.set(url, readProfile({ ...r, url }, company, entity, job));
          }
        }
      }
    };
    const inside = () => [...seen.values()].filter((p) => p.name && p.works_at_target);
    const where = city || "France";
    const team = job.team.slice(0, 3).join(" ") || "data";
    // entonnoir : même poste au même endroit, élargi au pays s'il y a peu de monde ; puis qui manage et qui recrute
    // requêtes courtes par mots-clés : le moteur répond mal aux phrases longues
    const queries = [`${company} ${entity} ${job.phrase || "data"} ${where}`];
    await run(queries);
    // + les anciens de l'école dans l'entreprise, toujours cherchés : ce sont les contacts prioritaires
    const next = [`${company} Agrocampus Ouest Institut Agro Rennes-Angers`,
      `${company} ${entity} ${team} manager ${where}`, `${company} talent acquisition recrutement ${where}`];
    if (inside().length < 6 && city) next.unshift(`${company} ${entity} ${job.phrase || "data"} France`);
    await run(next);
    queries.push(...next);
    // seules comptent les personnes qui travaillent aujourd'hui dans l'entreprise
    return [{ people: inside().slice(0, 30), searched: queries.length }, 200];
  });
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
