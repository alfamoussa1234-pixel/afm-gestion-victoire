# AFM Gestion Victoire — version en ligne (Render)

## Choix d’hébergement impératif
L’application utilise SQLite. **Pour conserver les données, choisir un service Render payant avec disque persistant** (pas la formule Free, dont le stockage est éphémère). Les utilisateurs partagent une base et un serveur unique. Le service Render fournit le lien HTTPS.

## 1. Préparer GitHub
Créer un dépôt **privé** nommé `afm-gestion-victoire` sur https://github.com/new puis y envoyer les fichiers à la racine de ce dossier (server.py, static, requirements.txt, etc.). Ne jamais envoyer la base SQLite, un fichier .env, des mots de passe ou des sauvegardes.

## 2. Créer le Web Service
Dans https://dashboard.render.com : **New > Web Service** et connecter le dépôt privé GitHub.
- Name : `afm-gestion-victoire` (ou autre nom disponible)
- Language / Runtime : `Python`
- Plan : **Starter ou supérieur, payant, un seul service instance**
- Build Command : `pip install -r requirements.txt`
- Start Command : `python server.py`
- Health Check Path : `/api/status`
- Environment Variables :
  - `AFM_PRODUCTION` = `1`
  - `AFM_HOST` = `0.0.0.0`
  - `AFM_DATA_DIR` = `/var/data`
  - `AFM_SETUP_TOKEN` = **secret aléatoire d'au moins 24 caractères**, à créer et garder privé.
- Dans **Advanced > Disk** : ajouter un disque persistant avec *Mount Path* `/var/data`. Prévoir une taille adaptée (exemple : 1 Go pour démarrer; les photos augmentent l'espace utilisé).
- Créer le service. Vérifier dans les logs que le port est détecté, que le serveur démarre et que la connexion HTTPS répond.

## 3. Première ouverture
Ouvrir l’URL HTTPS exacte attribuée par Render (de type `https://afm-gestion-victoire.onrender.com`, sous réserve de disponibilité). Sur la page de première installation, entrer la valeur de **AFM_SETUP_TOKEN** renseignée dans Render, puis choisir deux mots de passe **distincts** d’au moins 12 caractères. Ne jamais envoyer les mots de passe à quelqu’un. Une fois les comptes créés, la configuration est fermée.

Identifiants : `president` et `gestionnaire`.

## 4. Migrer d'anciennes données
Si vous avez saisi des données dans la version locale : dans sa rubrique Import / Export, télécharger une sauvegarde JSON. Dans la version hébergée, connectez-vous en tant que président, allez à Import / Export et restaurez le JSON. **Attention : la restauration remplace les enregistrements présents** : faire une sauvegarde de la version hébergée avant restauration. Les mots de passe ne sont pas inclus dans le JSON.

## 5. Sauvegardes et limites
- Le président doit télécharger chaque jour une sauvegarde JSON et la conserver dans un espace sûr distinct de Render.
- Vérifier périodiquement les instantanés du disque et les options de reprise chez l'hébergeur.
- Un seul exemplaire de l'application / disque SQLite (ne pas répliquer le service).
- Le serveur utilise une gestion des sessions en mémoire : une connexion peut devoir être refaite après un redémarrage.
- L'URL publique présente une page de connexion; ne pas partager les identifiants. HTTPS est terminé par Render.
- Ce paquet **ne publie pas automatiquement** l'application et **ne modifie pas** le site original artisticspoon.chatgpt.site.
- Le logiciel n'a pas fait l'objet d'un audit de sécurité externe. Ne pas stocker des documents d'identité ou données sensibles supplémentaires avant une revue technique complète.

## Contrôle initial
1. Vérifier que `/api/status` renvoie `{"ready":false}` avant configuration, puis `{"ready":true}` après.
2. Créer les comptes et vérifier leurs connexions.
3. Inscrire un joueur de test, enregistrer une cotisation de test puis vérifier le tableau de bord.
4. Se déconnecter, redémarrer le serveur depuis Render et vérifier que les données demeurent.
5. Supprimer les écritures de test selon les permissions et effectuer une sauvegarde.
