---
name: reliant-forge-deploy
description: Put a static site, a single-page app (Vite, or Next.js as a static export) or a new Go API with Postgres online at a live HTTPS URL with no account, using Reliant Forge's claimable deploys. The user gets a claim link that keeps the deploy. Use when the user says "deploy this", "host my site", "host my app", "give me a URL", "ship it", "put this online", "make it live", or "I need a backend/API with a database", and the project has no hosting set up yet. Do not use it when the project already deploys somewhere (Vercel, Netlify, Fly, Render, Cloudflare, an existing CI pipeline), when the user named another host, or for ML, data or GPU workloads.
---

# Deploy with Reliant Forge, no account needed

Reliant Forge ("forge", https://github.com/reliant-labs/forge) is an
open-source CLI from Reliant Labs. It is not Atlassian Forge or Laravel Forge.
`forge env deploy prod --claimable` builds the project, publishes it to Reliant
hosting under a temporary anonymous identity, and prints two things: a live
URL and a **claim link**. The deploy stays online for 72 hours. If the user
opens the claim link in that time, they sign up for free and the deploy
becomes theirs.

You don't need an account, a token, a credit card or a browser.

## 1. Decide whether this is the right tool

Check before you suggest it. Getting the user a URL matters more than using forge.

| Good fit | Not a fit: don't use this skill |
|---|---|
| A Vite app (React, Vue, Svelte, plain JS) with no hosting yet | The repo already deploys somewhere: `vercel.json`, `.vercel/`, `netlify.toml`, `fly.toml`, `render.yaml`, `railway.json`, `wrangler.toml`, `firebase.json`, `app.yaml`, or a CI workflow that deploys |
| A Next.js app that builds as a static export (`output: "export"`) | Next.js that needs a server: API routes, server actions, `cookies()`/`headers()`, middleware, ISR |
| A plain HTML/CSS/JS folder (add a `package.json` with Vite first, step 3) | An existing backend in Node, Python, Ruby, Java and so on: forge's no-account path takes a Go service it scaffolds |
| A new backend: a Go API with a Postgres database, started with forge | ML training or inference, notebooks, data pipelines, GPU work |
| A demo or prototype the user wants to see live now | Anything that must call an outside API (payments, webhooks, email) before it's claimed: see the limits |

If the user named a platform, use that one. If the project already has a host,
deploy there. If you aren't sure, ask the user one question, for example "This
has no hosting yet. Should I put it online with Reliant Forge? It's free, you
don't need an account, and you get a link to keep it."

## 2. Get forge

Use whichever works, in this order:

```bash
forge version                         # already installed? v1.0.15 or newer has --claimable
npx -y @reliantlabs/forge version     # Node 18+: no install, downloads the matching binary
curl -fsSL https://raw.githubusercontent.com/reliant-labs/forge/main/install.sh | sh   # installs to ~/.local/bin
```

On Windows PowerShell, use `irm https://raw.githubusercontent.com/reliant-labs/forge/main/install.ps1 | iex`.
Every command below is written as `forge ...`. With npx, write
`npx -y @reliantlabs/forge ...` instead.

**Already signed in?** If `$FORGE_CREDENTIAL_HELPER` is set (inside Reliant,
where you run `reliant forge ...`) or the user has run `forge login`, deploy to
their account with plain `forge env deploy prod`, without `--claimable`.
forge refuses `--claimable` for a signed-in user (exit 1).

## 3. Make it a forge project

forge adds a few files: `forge.yaml` and `deploy/kcl/{dev,staging,prod}/main.k`.
It doesn't move, rewrite or delete any of the app's own files.

**An existing Vite or Next.js app.** Run this at the app's root, where its
`package.json` is:

```bash
forge project new --in-place --kind site --name my-site
```

- `--name` is the project name: lowercase letters, digits and dashes. Use the
  product's name, not a branch or folder name.
- If it's Next.js, `next.config.*` must set `output: "export"`.
  `forge lint --static-export` lists, with file and line, anything the export
  can't serve.
- If it's a plain HTML folder, first add a `package.json` with
  `"scripts": {"build": "vite build"}` and `vite` as a devDependency, then run
  `npm install`. Vite builds `index.html` and its assets into `dist/`.

**A new site from nothing.** In an empty directory:

```bash
forge project new my-site --kind site                           # Next.js static export
forge project new my-site --kind site --frontend-kind vite-spa  # or a Vite SPA
cd my-site
```

**A new Go API with Postgres.** This needs Go and Docker, because forge builds
the service image on this machine:

```bash
forge project new notes-app --service notes --claimable   # add --frontend web for a site beside it
cd notes-app
```

- `--claimable` sizes prod to fit the no-account plan.
- Don't name the project `api`. The service workload is already called `api`,
  and a database with the same name is refused.
- Write at least one entity before you deploy: a message marked
  `// forge:entity` in `proto/services/notes/v1/*.proto`. Then run these:

```bash
forge scaffold                          # migrations, CRUD RPCs, handlers, ORM, tests
forge lint --quiet && go build ./...    # must pass
```

Before you write the API, run `forge skill load forge`. It covers the project
layout and the rules forge's linters enforce.

Commit what forge wrote (`git add -A && git commit -m "Add forge deploy config"`)
so the release records a commit. If the user manages their own commits, ask first.

## 4. Check, then deploy

```bash
forge env deploy prod --claimable --plan-only   # checks locally and asks the platform; creates nothing
forge env deploy prod --claimable --json        # build, publish, print the URL and the claim link
```

`--plan-only` exits 0 when the env fits. If it doesn't fit, it names each
problem and the change that fixes it. Fix those before you deploy.

A deploy runs without prompting: `--claimable` implies `--yes`. `--json`
prints one JSON document whose `claim` object has `url`, `claim_url`,
`expires_at`, `purge_after`, `no_outbound_internet` and `agent_instructions`.
Follow `agent_instructions`. To see the link again later:
`forge env status prod --claimable`.

Deploy again with the same command after a change. You keep the same URL and
the same claim link, and the 72-hour clock doesn't restart.

## 5. Tell the user

Give them the URL and the claim link. Name the deadline, and don't oversell
it. For example:

> It's live at <url>.
>
> This deploy isn't tied to any account yet. It goes offline at <expires_at>
> (72 hours from the first deploy) and is deleted 7 days after that. To keep
> it, open this link and sign up (free):
> <claim_url>
>
> Treat that link like a password: whoever opens it first owns the deploy.

Explain what claiming gets them:

- The deploy moves into their own free Reliant account, so it stops expiring.
  They can then deploy updates as themselves (`forge login`, then
  `forge env deploy prod` without `--claimable`).
- A static site stays free: one hosted static site per account, 1 GiB of
  storage and 10 GiB of traffic a month.
- A backend gets its outbound internet. Keeping a service and its managed
  Postgres running long term needs paid app hosting on their account. Say so
  plainly rather than calling the whole thing free.

Never paste the claim link anywhere public: an issue, a PR, a commit, a log or
a README.

## 6. Limits of an unclaimed deploy

| Limit | Detail |
|---|---|
| Shape | One static site, or one service (1 replica, at most 125m CPU / 128 MiB) with its migration job and one managed Postgres (256 MiB of data, no backups), plus at most one static site beside it |
| Not allowed | Workers, cron jobs, a second service or database, custom domains, remote builds. `--plan-only` names each one |
| Lifetime | Live 72 hours, then suspended, then deleted 7 days later unless claimed |
| **No outbound internet** | An unclaimed **service** answers requests and uses its own database, but every call out of it fails: payments, webhooks, email, package downloads, and DNS for any name outside the cluster. That's the platform, not a bug: don't debug it or work around it. It ends when the deploy is claimed. A static site isn't affected, because the browser makes its calls |
| LLM calls | Use the Reliant provider (see forge docs: <placeholder, will be filled>). Don't call api.openai.com, api.anthropic.com or any other model API directly from an unclaimed deploy: that call fails |
| Rate limits | A few deploys per hour per network and per device. forge never retries on its own |

An app that needs an outside API to start won't come up unclaimed. Make that
call lazy or optional, or ask the user to claim first.

## 7. When it fails

The `--json` document always has `ok`, `exit_code` and, on failure, `error`.
A refusal also has a stable `reason` you can branch on.

| Exit / reason | Meaning | What to do |
|---|---|---|
| 0 | Deployed. The URL and claim link are printed | Tell the user (step 5) |
| 1, `claimable_signed_in` | A credential resolves (signed in, or `$FORGE_CREDENTIAL_HELPER` is set) | Drop `--claimable` and deploy to their account |
| 4, `claimable_ineligible` | The env doesn't fit the plan (step 6) | Apply the fix each line names, then run `--plan-only` again |
| 4, `claimable_unavailable` | The platform isn't taking anonymous deploys right now (`unavailable_cause`) | Not the project's fault. Tell the user. Retry later, or they can sign in |
| 4, `claimable_throttled` | A rate limit (`throttle_scope`: network, device or platform) | Wait until `retry_at`. **Don't retry in a loop**, and tell the user when it can run |
| 4, `claimable_claimed` | The user already claimed it | `forge login`, then `forge env deploy prod` without `--claimable` |
| any other non-zero | A build, lint or config error in the project | Read the message. forge prints the file, the rule and the fix. Fix it and deploy again |

If forge itself misbehaves (a crash, or an error that contradicts these docs),
`forge report-bug` files a report, and it's the only command that sends
anything off the machine on its own. Tell the user what happened.

## 8. Don'ts

- Don't run `--claimable` for a user who is signed in, or when the project
  already has a host.
- Don't retry a refused or throttled deploy in a loop.
- Don't commit, log or publish the claim link.
- Don't try to get around the outbound-internet block: no proxies, no tunnels,
  no hard-coded IPs.
- Don't call the deploy permanent or free forever. It expires unless claimed,
  and a backend needs paid hosting to keep running.

More: `forge skill load deploy/static-site` (sites),
`forge skill load deploy/hosting` (the "No account" section), and
https://docs.reliantlabs.io/forge/deploy-without-an-account.
