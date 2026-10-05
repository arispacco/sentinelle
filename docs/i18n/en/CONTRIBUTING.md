# Contributing to Sentinelle

Thanks for coming. If you have never opened a pull request, you are in the right place: each simple task asks for **a single new file**. So two people never edit the same line, and there is no conflict to untangle.

The French version ([`CONTRIBUTING.md`](../../../CONTRIBUTING.md)) is the reference.

Tasks marked **Plus tard** (Later) already require reading the code. Leave them for when branches and reviews have become second nature. During the workshop, only take an issue with the `good first issue` label.

## Choosing a task

1. Open the ["good first issue" issues](https://github.com/arispacco/sentinelle/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).
2. Read the requested file. If it already exists on `main`, or if someone has commented « Je la prends » ("I'll take it"), pick another issue.
3. Comment **Je la prends** under the issue. One person per issue.

If all the simple issues are taken, your first pull request is still possible without conflict: copy [`participants/EXEMPLE.md`](../../../participants/EXEMPLE.md) to `participants/ton-prenom-ton-nom.md` (your first and last name). One file per person. Do not add your name to a shared file.

## The seven steps

Do them in order. Wait until each one is finished before starting the next.

### 1. Fork

On GitHub, open [arispacco/sentinelle](https://github.com/arispacco/sentinelle) and click **Fork**. You get a copy on your account.

### 2. Clone

In Git Bash (Windows) or the terminal:

```bash
git clone https://github.com/TON-COMPTE/sentinelle.git
cd sentinelle
```

Replace `TON-COMPTE` with your GitHub username.

### 3. Branch

Never commit on `main`.

```bash
git switch -c docs/readme-en
```

The suggested branch name is written in the issue. `git switch -c` creates the branch and switches to it.

### 4. The file

Create **only** the file named in the issue. Do not modify `README.md`, the file of another language, or this guide.

Example for the English translation of the README:

```bash
mkdir -p docs/i18n/en
```

Then save the text in `docs/i18n/en/README.md`.

### 5. Commit

First check that Git knows who you are. The email must be the one of your GitHub account (or GitHub's `noreply` address), otherwise the contribution will not show up on your profile.

```bash
git config --global user.name "Ton Nom"
git config --global user.email "ton-email@exemple.com"
```

```bash
git add docs/i18n/en/README.md
git status
git commit -m "docs: traduire le README en anglais"
```

`git add` chooses what goes into the snapshot. `git commit` records it. Always keep the `-m` option: without it, a Vim editor may open (`Esc` then `:q!` to quit without saving anything).

`git status` should show a single added file.

### 6. Push

```bash
git push -u origin docs/readme-en
```

GitHub no longer accepts your account password in the terminal. If authentication fails:

- either `gh auth login`;
- or a **Personal Access Token (classic)** with the `repo` scope, pasted in place of the password (Settings → Developer settings → Personal access tokens).

### 7. Pull request

On your fork, GitHub offers **Compare & pull request**. If the banner has disappeared: **Pull requests** tab → **New pull request**, and choose your branch.

- Title: the one suggested in the issue.
- Description: link to the issue, for example `Fixes #12` (the number is the issue's number).
- Check that the base is `arispacco/sentinelle`, branch `main`, and that your branch comes from your fork.

## Rules that prevent conflicts

- One issue = one new file = one branch = one pull request.
- You do not modify a file that another issue also asks for.
- You do not push secrets (`.env`, key, token, cookie, Telegram session).
- You proofread the text. An AI tool can help you understand, not send a translation you have not read.
- For a fix requested by the maintainer, you commit on **the same branch** and then `git push` again. Do not open a second pull request.

## If something gets stuck

| Message | What to do |
| --- | --- |
| `git: command not found` | Install Git, close the terminal, reopen Git Bash. |
| `Please tell me who you are` | The two `git config --global` commands above. |
| `Authentication failed` | Token or `gh auth login`. Your GitHub password does not work here. |
| `fatal: not a git repository` | `cd sentinelle` |
| `rejected` on push | `git pull --rebase origin ta-branche` then `git push` |
| Commit made on `main` by mistake | `git switch -c ma-branche` (the commit follows the new branch) |
| No pull request banner | Pull requests → New pull request |
| `LF will be replaced by CRLF` | Harmless message on Windows |

Plan B if installing Git fails: on the GitHub repository, press `.` to open github.dev and make the pull request from the browser. The single-file rule still applies.

## After the workshop

Issues **without** the `good first issue` label describe real fixes (container port, missing tests, simulator prices). They are meant for the rest of the month, one person at a time, after reading the code cited in the issue.
