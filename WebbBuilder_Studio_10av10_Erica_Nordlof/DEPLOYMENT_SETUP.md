# Integration setup

## 1. OpenAI

Servervariabel:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5
```

Nyckeln ska aldrig ligga i frontend eller i ett genererat repository.

---

## 2. GitHub

Servervariabler:

```text
GITHUB_TOKEN=...
GITHUB_OWNER=EricaNordlof
```

Rekommenderat:

- använd fine-grained token,
- begränsa tokenens åtkomst så mycket som möjligt,
- tillåt repository administration om nya repositories ska skapas,
- tillåt contents write för att pusha filer.

Repositories skapas privata som standard.

---

## 3. Render

```text
RENDER_API_KEY=...
RENDER_OWNER_ID=...
RENDER_REGION=frankfurt
```

`RENDER_OWNER_ID` är workspace-ID från Render.

Buildern kan:

- skapa static site,
- skapa web service,
- skapa Docker-baserad web service,
- trigga nya deployer.

Privata GitHub-repositories kräver att Render-workspacet har repoåtkomst.

---

## 4. Vercel

```text
VERCEL_TOKEN=...
VERCEL_TEAM_ID=
```

För personligt konto kan `VERCEL_TEAM_ID` lämnas tom.

För deploy från privat GitHub-repository måste GitHub-integrationen i Vercel ha åtkomst.

---

## 5. Builder security

```text
APP_PASSWORD=...
SESSION_SECRET=...
SESSION_SECURE=true
```

För lokal HTTP-utveckling:

```text
SESSION_SECURE=false
```

För HTTPS-produktion:

```text
SESSION_SECURE=true
```
