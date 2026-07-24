# WebbBuilder Studio v2

En förenklad version av WebbBuilder Studio med fokus på kärnflödet:

1. Beskriv en hemsida eller webbapp.
2. Starta AI-generering.
3. Servern svarar direkt och genereringen körs som bakgrundsjobb.
4. Webbläsaren pollar status i stället för att hålla ett långt HTTP-anrop öppet.
5. Förhandsgranska resultatet.
6. Finslipa med en ny instruktion.
7. Ladda ner hela projektet som ZIP.

## Medvetet borttaget i v2

- Automatisk GitHub-publicering.
- Automatisk Render/Vercel-publicering.
- Stripe.
- Fleranvändar-SaaS.
- Komplicerade integrationskedjor.

De funktionerna läggs tillbaka först när kärnflödet är stabilt.

## Start lokalt

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Installera:

```bash
pip install -r requirements.txt
```

Sätt miljövariabler enligt `.env.example`.

Starta:

```bash
uvicorn app.main:app --reload
```

Öppna:

```text
http://127.0.0.1:8000
```

## Render

Om denna mapp ligger inuti ditt befintliga repo:

Blueprint Path:

```text
WebbBuilder_Studio_v2/render.yaml
```

Alternativt kan du skapa ett separat repo där innehållet i denna mapp ligger i repo-roten och använda `render.yaml` direkt.

Miljövariabler som måste fyllas:

```text
APP_PASSWORD
OPENAI_API_KEY
```

Render genererar `SESSION_SECRET`.

## Varför v2 inte ska fastna på samma sätt som v1

När du trycker på Generera skickas ett kort API-anrop:

```text
POST /api/projects/{id}/generate
```

Det startar ett bakgrundsjobb och svarar direkt.

Frontend pollar sedan:

```text
GET /api/projects/{id}/status
```

varannan sekund.

Det långa OpenAI-anropet ligger alltså inte kvar som ett öppet formuläranrop i webbläsaren.

## Viktigt om Render Free

`render.yaml` använder SQLite i `/tmp`.

Data kan därför försvinna vid omstart eller ny deploy. Detta är avsiktligt för testversionen. När v2 fungerar stabilt bör lagringen bytas till PostgreSQL eller annan persistent databas.

## OpenAI

Standardmodell:

```text
gpt-5-mini
```

Structured Outputs används via Responses API med JSON-schema så att AI:n returnerar ett projektmanifest med:

- summary
- files
- notes

API-nyckeln används endast på serversidan.
