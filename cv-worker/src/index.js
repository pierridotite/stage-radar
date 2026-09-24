/**
 * Analyse de CV pour le Radar Stages : reçoit le texte d'un CV (extrait dans le navigateur de l'élève),
 * demande à un petit modèle OpenAI une fiche structurée, et la renvoie. Rien n'est stocké ni journalisé.
 *
 * Garde-fous : origine du site obligatoire, 5 analyses par minute et par visiteur, texte plafonné,
 * réponse plafonnée, schéma JSON strict (le modèle ne peut répondre que par les identifiants du dictionnaire).
 * La clé OpenAI est un secret Cloudflare (wrangler secret put OPENAI_API_KEY), jamais dans le code.
 */

const MAX_CHARS = 15000;
const ID = /^[a-z_]{1,24}$/;

const SYSTEM = `Tu lis le CV d'un·e élève ingénieur·e en science des données (Institut Agro Rennes-Angers) qui cherche
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

function schema(lex) {
  const arr = (e) => ({ type: "array", items: { type: "string", enum: e } });
  const props = {
    typology: { type: "string" },
    summary: { type: "string" },
    skills: arr(lex.skills),
    domains: {
      type: "array",
      items: {
        type: "object",
        properties: { id: { type: "string", enum: lex.domains }, strength: { type: "string", enum: ["forte", "moyenne", "faible"] } },
        required: ["id", "strength"], additionalProperties: false,
      },
    },
    target_roles: arr(lex.roles),
    languages: arr(lex.languages),
    experience_months: { type: "integer" },
  };
  return { type: "object", properties: props, required: Object.keys(props), additionalProperties: false };
}

function cleanIds(list) {
  return Array.isArray(list) ? [...new Set(list.filter((x) => typeof x === "string" && ID.test(x)))].slice(0, 80) : [];
}

async function callOpenAI(env, model, text, lex) {
  const body = {
    model,
    messages: [{ role: "system", content: SYSTEM }, { role: "user", content: `CV :\n${text}` }],
    response_format: { type: "json_schema", json_schema: { name: "profil_cv", strict: true, schema: schema(lex) } },
    max_completion_tokens: 1500,
  };
  if (model.startsWith("gpt-5")) body.reasoning_effort = "minimal";
  return fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

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

    if (env.CV_RATE_LIMITER) {
      const ip = request.headers.get("CF-Connecting-IP") || "inconnu";
      const { success } = await env.CV_RATE_LIMITER.limit({ key: `cv:${ip}` });
      if (!success) return reply({ error: "Trop d'analyses d'affilée : réessaie dans une minute." }, 429);
    }
    if (Number(request.headers.get("Content-Length") || 0) > 200000) return reply({ error: "Requête trop volumineuse." }, 413);

    let payload;
    try { payload = await request.json(); } catch { return reply({ error: "Requête illisible." }, 400); }
    const text = String(payload.text || "").slice(0, MAX_CHARS).trim();
    if (text.length < 200) return reply({ error: "Le CV ne contient pas assez de texte." }, 400);
    const lex = {
      skills: cleanIds(payload.lexicon?.skills), domains: cleanIds(payload.lexicon?.domains),
      roles: cleanIds(payload.lexicon?.roles), languages: cleanIds(payload.lexicon?.languages),
    };
    if (!lex.skills.length || !lex.domains.length || !lex.roles.length) return reply({ error: "Dictionnaire manquant." }, 400);
    if (!lex.languages.length) lex.languages = ["aucune"];

    let model = env.MODEL || "gpt-5-nano";
    let r = await callOpenAI(env, model, text, lex);
    if (r.status === 404 && env.FALLBACK_MODEL) {
      model = env.FALLBACK_MODEL;
      r = await callOpenAI(env, model, text, lex);
    }
    if (!r.ok) return reply({ error: "Le service d'analyse ne répond pas, réessaie plus tard." }, 502);
    const data = await r.json();
    const msg = data.choices?.[0]?.message;
    if (!msg?.content || msg.refusal) return reply({ error: "Analyse impossible pour ce CV." }, 502);
    let profile;
    try { profile = JSON.parse(msg.content); } catch { return reply({ error: "Réponse d'analyse illisible." }, 502); }
    return reply({ profile, model });
  },
};
