# Serveur du Radar Stages (Cloudflare Worker)

Le tableau de bord est un site statique et public : il ne peut contenir aucune clé. Ce petit serveur (gratuit chez
Cloudflare pour ce volume) garde les clés en secret et rend deux services, **uniquement quand l'élève clique** :

| Service | Ce qu'il fait | Coût |
|---|---|---|
| `POST /cv` (« Affiner avec l'IA ») | reçoit le texte du CV extrait dans le navigateur, demande à `gpt-5-nano` la typologie du profil, les compétences, domaines et métiers visés (schéma JSON strict, identifiants de `config/fit.yaml`) | ~0,001 $ par CV |
| `POST /contacts` (« Trouver des contacts automatiquement ») | construit 3 recherches à partir de l'offre (anciens agro dans l'entreprise, équipe data ou équipe de l'offre, recrutement), interroge le moteur **Tavily** limité aux profils LinkedIn publics, puis `gpt-5-nano` décrit chaque personne (poste, entité, école, rôle data, recruteur) ; la page calcule le score de match avec `config/network.yaml` | 3 recherches Tavily (1 000 gratuites / mois) + ~0,001 $ |

**Rien n'est stocké ni journalisé** côté serveur. LinkedIn n'est pas aspiré : les profils viennent de l'index public
d'un moteur de recherche, comme une recherche Google faite à la main. Les résultats restent 7 jours dans le navigateur
de l'élève, pour ne pas relancer les mêmes recherches.

Garde-fous : seules les requêtes venant du site sont acceptées ; 5 demandes par minute, par visiteur et par service ;
les requêtes de recherche sont construites par le serveur (impossible de s'en servir pour chercher autre chose) ;
entrées et réponses plafonnées.

## Mise en service (une fois, 10 minutes)

1. Créer une clé Tavily gratuite, sans carte bancaire : https://app.tavily.com (plan « Researcher », 1 000 recherches / mois).
2. Avoir un compte Cloudflare gratuit et Node.js, puis :

```bash
cd cv-worker
npx wrangler login                        # ouvre le navigateur pour se connecter à Cloudflare
npx wrangler secret put OPENAI_API_KEY    # colle la clé OpenAI
npx wrangler secret put TAVILY_API_KEY    # colle la clé Tavily
npx wrangler deploy                       # affiche l'adresse du service : https://radar-stages.<compte>.workers.dev
```

3. Reporter cette adresse dans `config/llm.yaml` (`worker_url: "https://radar-stages.<compte>.workers.dev"`) et
   pousser : les boutons « Affiner avec l'IA » et « Trouver des contacts automatiquement » apparaissent au prochain
   passage du workflow.

Tests (OpenAI et Tavily simulés) : `npm test`.
