# WebbBuilder Studio 10/10

Privat AI-byggstudio för att gå från idé till komplett kod, revisioner, kvalitetskontroll, GitHub och deployment.

## Vad appen gör

1. Du beskriver vad du vill bygga på vanlig svenska.
2. AI:n skapar ett komplett projekt.
3. Du granskar projektet i sandboxad preview.
4. Du finslipar med nya instruktioner.
5. Varje ändring sparas som en revision.
6. Du kan jämföra revisioner med diffvy.
7. Preflight söker efter bland annat:
   - möjliga hårdkodade hemligheter,
   - kvarlämnade platshållare,
   - saknad README,
   - saknad `.env.example`,
   - saknad viewport-meta,
   - saknad preview.
8. Buildern kan skapa/synka ett GitHub-repository.
9. Buildern kan starta deployment till Render eller Vercel.
10. När projektet är stabilt kan auto-publicering aktiveras för nya revisioner.

## Viktiga säkerhetsval

- API-nycklar ligger endast på builder-servern.
- Genererad backendkod körs inte automatiskt i preview.
- Preview sker i sandboxad iframe.
- Publicering blockeras om preflight hittar mönster som ser ut som privata nycklar eller API-hemligheter.
- GitHub-repositories skapas privata som standard.
- Hemligheter ska ligga som miljövariabler, aldrig i genererad frontendkod.
- Buildern sätter grundläggande säkerhetsheaders.
- Unsafe cross-origin POST/PUT/PATCH/DELETE blockeras med Origin-kontroll.

## Start lokalt

```bash
python -m venv .venv

# macOS/Linux
source .venv/bin/activate

# Windows
# .venv\Scripts\activate

pip install -r requirements.txt
```

Kopiera:

```text
.env.example
```

till dina miljövariabler eller använd en lokal miljöhanterare.

Start:

```bash
uvicorn app.main:app --reload
```

Öppna:

```text
http://127.0.0.1:8000
```

## Nödvändigt för AI

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5
```

Modellnamnet är konfigurerbart.

## GitHub – automatisk repositoryleverans

Sätt:

```text
GITHUB_TOKEN=...
GITHUB_OWNER=EricaNordlof
```

Token behöver rättigheter för att:

- skapa repository om buildern ska skapa nya repositories,
- skriva repository contents / Git-objekt.

Buildern:

- skapar repository vid behov,
- skapar blobs för alla filer,
- skapar ett komplett Git-tree,
- skapar en samlad commit,
- uppdaterar standardbranch,
- sparar repo-URL och commit SHA på projektet.

## Render

Sätt:

```text
RENDER_API_KEY=...
RENDER_OWNER_ID=...
RENDER_REGION=frankfurt
```

Första publiceringen kan skapa en tjänst från GitHub.

Buildern försöker automatiskt välja:

- statisk site för HTML/Vite-liknande projekt,
- Node web service för Next/Node,
- Python web service för FastAPI/Python,
- Docker web service när `Dockerfile` finns.

Efter att en Render service har skapats sparas service-ID:t och kommande publiceringar kan trigga nya deployer med senaste Git commit.

### Viktigt om privat GitHub-repo

Render-kontot/workspacet måste ha behörighet att läsa repositoryt.

## Vercel

Sätt:

```text
VERCEL_TOKEN=...
VERCEL_TEAM_ID=
```

`VERCEL_TEAM_ID` är valfritt för personligt konto.

Buildern skapar en deployment från projektets GitHub-repository.

För privat GitHub-repo måste Vercels GitHub-integration ha åtkomst till repositoryt.

## Auto-publicering

Auto-publicering är avstängd som standard.

När den aktiveras kan varje ny revision automatiskt:

```text
AI/refinering
→ revision sparas
→ preflight
→ GitHub commit
→ Render/Vercel deploy
```

Aktivera först när projektet är stabilt.

## Render-hosting av själva buildern

Projektet innehåller `render.yaml`.

1. Lägg WebbBuilder Studio i ett privat GitHub-repo.
2. Skapa Blueprint på Render.
3. Sätt hemliga miljövariabler.
4. Deploy.

### SQLite-varning

Den här versionen använder SQLite för enkel installation.

På Render Free eller annan ephemeral hosting kan lokal SQLite-data försvinna vid omstart/redeploy.

För långsiktig produktion bör du byta till PostgreSQL och persistent object storage.

## Filer

- `app/main.py` – routes och pipeline
- `app/generator.py` – AI-generering/refinering
- `app/integrations.py` – GitHub, Render, Vercel
- `app/quality.py` – preflight
- `app/db.py` – projekt, revisioner och deployhistorik
- `app/templates/` – UI
- `app/static/` – CSS/JS

## Arbetsflöde

```text
Nytt projekt
   ↓
Beskriv önskemål
   ↓
Generera komplett version
   ↓
Preview
   ↓
Finslipa
   ↓
Revisioner + diff
   ↓
Preflight
   ↓
GitHub
   ↓
Render/Vercel
   ↓
Live URL
```

## Vad buildern inte lovar automatiskt

AI-genererad kod ska fortfarande granskas innan skarp användning när projektet innehåller exempelvis:

- betalningar,
- autentisering,
- personuppgifter,
- filuppladdning,
- juridiska avtal,
- känslig data,
- avancerade databasmigrationer.

Preflight är ett säkerhetsnät, inte en fullständig säkerhetsrevision.
