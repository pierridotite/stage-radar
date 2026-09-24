# Analyse des CV par IA (Cloudflare Worker)

Le tableau de bord est un site statique et public : il ne peut pas contenir de clé OpenAI. Ce petit serveur
(gratuit chez Cloudflare pour ce volume) garde la clé en secret et fait l'analyse des CV :

1. le navigateur de l'élève extrait le texte du CV (PDF, Word) et l'envoie ici, seulement si l'élève clique
   « Affiner avec l'IA » ;
2. le serveur demande au modèle (`gpt-5-nano`) une fiche structurée : typologie du profil, compétences, domaines,
   métiers visés, langues ; le schéma JSON est strict et n'accepte que les identifiants de `config/fit.yaml` ;
3. il renvoie la fiche, qui sert au calcul du fit dans la page. **Rien n'est stocké ni journalisé.**

Garde-fous : seules les requêtes venant du site sont acceptées, 5 analyses par minute et par visiteur,
texte limité à 15 000 caractères, réponse limitée. Coût : environ 0,001 $ par CV.

## Mise en service (une fois, 5 minutes)

Il faut un compte Cloudflare gratuit et Node.js.

```bash
cd cv-worker
npx wrangler login                        # ouvre le navigateur pour se connecter à Cloudflare
npx wrangler secret put OPENAI_API_KEY    # colle la clé OpenAI quand elle est demandée
npx wrangler deploy                       # affiche l'adresse du service, en https://....workers.dev
```

Puis reporter cette adresse dans `config/llm.yaml` :

```yaml
cv:
  api_url: "https://radar-stages-cv.<compte>.workers.dev"
```

et pousser la modification : le bouton « Affiner avec l'IA » apparaît dans le tableau de bord au prochain
passage du workflow. Sans cette adresse, l'analyse du CV reste faite localement par les règles.
