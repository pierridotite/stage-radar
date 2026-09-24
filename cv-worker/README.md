# Serveur du Radar Stages (Cloudflare Worker)

Le tableau de bord est un site statique et public : il ne peut contenir aucune clé. Ce petit serveur (gratuit chez
Cloudflare pour ce volume) garde les clés en secret et rend deux services, **uniquement quand l'élève clique** :

| Service | Ce qu'il fait | Coût |
|---|---|---|
| `POST /cv` (au dépôt du CV) | reçoit le texte du CV extrait dans le navigateur et demande à `gpt-5-nano` les filtres du profil : typologie, compétences, métiers, secteurs, régions, langues (identifiants fournis par la page, schéma JSON strict) et jusqu'à 12 thèmes précis avec synonymes | ~0,001 $ par CV |
| `POST /contacts` (« Trouver des contacts ») | recherche en entonnoir sur le moteur **Tavily**, limitée aux profils LinkedIn publics (`linkedin.com/in`) : même poste dans la ville de l'offre, puis en France, managers de l'équipe, recrutement, anciens de l'école dans l'entreprise ; lit **sans IA** le poste actuel, l'entreprise, le lieu et l'école, ne garde que les personnes de l'entreprise ; résultats gardés 7 jours par offre (Cache API) ; la page calcule le score avec `config/network.yaml` | 4 ou 5 recherches Tavily par offre (1 000 gratuites / mois), pas d'OpenAI |

Le score de fit et la note des offres sont calculés sans IA ; ce serveur n'est appelé que pour ces deux services.

**Aucun CV n'est stocké et rien n'est journalisé** côté serveur ; seuls les résultats publics de recherche de contacts sont gardés 7 jours en cache. LinkedIn n'est pas aspiré : les profils viennent de l'index public
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

3. Reporter cette adresse dans `config/server.yaml` (`worker_url: "https://radar-stages.<compte>.workers.dev"`) et
   pousser : l'analyse du CV par l'IA et le bouton « Trouver des contacts » sont actifs au prochain passage du workflow.
4. Après chaque modification de `src/index.js` : `npx wrangler deploy` (les secrets sont conservés).

Tests (OpenAI et Tavily simulés, aucun appel réel) : `npm test`.
