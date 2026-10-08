# Installer Git sur macOS

Ce guide s'adresse à une personne qui n'a jamais installé Git. À la fin, Git est installé, configuré avec ton nom et ton e-mail, et prêt pour les sept étapes de [CONTRIBUTING.md](../../CONTRIBUTING.md).

Toutes les commandes se tapent dans l'application **Terminal** (Applications → Utilitaires → Terminal, ou `Cmd` + `Espace` puis « Terminal »).

## 1. Vérifier si Git est déjà là

```bash
git --version
```

- Si une version s'affiche (par exemple `git version 2.39.5`), Git est installé : passe à l'étape 3.
- Si macOS propose d'installer les **outils de ligne de commande** (Command Line Tools de Xcode), passe à l'étape 2.

## 2. Installer Git

Choisis **une seule** des méthodes ci-dessous.

### Méthode A : outils de ligne de commande Xcode (la plus simple)

Sur macOS, la première commande `git` déclenche souvent une fenêtre qui propose d'installer les outils de ligne de commande. Clique sur **Installer**, accepte la licence et attends la fin du téléchargement. Tu n'as pas besoin d'installer l'application Xcode complète.

Si la fenêtre n'apparaît pas, lance-la toi-même :

```bash
xcode-select --install
```

### Méthode B : Homebrew

Si tu utilises déjà [Homebrew](https://brew.sh) :

```bash
brew install git
```

### Autres options

La page officielle liste toutes les façons d'installer Git sur Mac : https://git-scm.com/download/mac

Une fois l'installation terminée, **ferme le Terminal et rouvre-le**, puis vérifie :

```bash
git --version
```

## 3. Dire à Git qui tu es

Git signe chaque commit avec un nom et un e-mail. Utilise **le même e-mail que ton compte GitHub** (ou l'adresse `noreply` de GitHub), sinon tes contributions n'apparaissent pas sur ton profil.

```bash
git config --global user.name "Ton Nom"
git config --global user.email "ton-email@exemple.com"
```

## 4. Vérifier la configuration

```bash
git --version
git config --list
```

Dans la liste, tu dois retrouver `user.name=Ton Nom` et `user.email=ton-email@exemple.com` avec tes vraies valeurs. Si la liste est longue, appuie sur `q` pour la quitter.

## 5. Le mot de passe GitHub ne sert pas dans le terminal

Au premier `git push`, GitHub refuse le mot de passe de ton compte. Il faut à la place :

- soit `gh auth login` ;
- soit un **Personal Access Token (classic)** collé à la place du mot de passe, par exemple `<ton-token>`.

Tout est expliqué à l'étape 6 de [CONTRIBUTING.md](../../CONTRIBUTING.md). Ne colle jamais ton token dans un fichier du dépôt.

## Si quelque chose bloque

| Message | Quoi faire |
| --- | --- |
| `git: command not found` | Refais l'étape 2, puis ferme et rouvre le Terminal. |
| `xcrun: error: invalid active developer path` | Les outils de ligne de commande manquent (souvent après une mise à jour de macOS) : `xcode-select --install`. |
| `Please tell me who you are` | Les deux commandes `git config --global` de l'étape 3. |
| `Authentication failed` | Token ou `gh auth login`, voir l'étape 5. |

Tu peux maintenant suivre les sept étapes de [CONTRIBUTING.md](../../CONTRIBUTING.md).
