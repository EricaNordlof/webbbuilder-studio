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

## Automatisk kvalitetskontroll och självreparation

Innan en AI-genererad version får status `READY` kör WebbBuilder nu en deterministisk kvalitetskontroll.

Den kontrollerar bland annat:

- att HTML/preview finns,
- saknade lokala CSS/JS/bildreferenser,
- externa bild-URL:er med riktigt HTTP-anrop,
- att externa bilder faktiskt svarar med `Content-Type: image/*`,
- tomma `img src`,
- bildkrav som anger ett tydligt antal bilder,
- uttryckliga hero-bildkrav,
- `Lorem ipsum` och `example.com`-platshållare,
- ogiltiga lokala binära bildfiler i det textbaserade projektmanifestet.

Flödet är:

```text
AI genererar/finslipar
        ↓
status: validating
        ↓
automatisk kvalitetskontroll
        ↓
Godkänd → READY
        ↓ nej
status: repairing
        ↓
AI får exakt lista på hittade fel
        ↓
ny kvalitetskontroll
        ↓
READY eller tydligt fel efter max antal reparationsförsök
```

Standard är högst två automatiska reparationsförsök. Ändras med:

```text
QUALITY_MAX_REPAIR_PASSES=2
QUALITY_HTTP_TIMEOUT=10
QUALITY_MAX_EXTERNAL_IMAGES=30
```

Automatiska reparationer skapar inte extra användarrevisioner. Bara den slutligt godkända versionen sparas som revision. Kvalitetskontrollens resultat läggs till under **Noteringar**.

Detta verifierar tekniska fel och konkreta bildkrav. Det kan däremot inte säkert avgöra om ett foto semantiskt föreställer exakt rätt motiv; sådan bildinnehållsanalys kräver en separat vision-kontroll.

## Publik projektportfölj + adminläge

Den här versionen har två åtkomstnivåer:

### Publik besökare

Kan utan inloggning:

- se projektlistan på `/`
- öppna varje projekt
- se senaste previewn

Kan inte:

- skapa projekt
- starta AI-generering
- finslipa projekt
- ladda ner ZIP
- ladda upp filer eller ZIP
- radera projekt
- läsa adminstatus-API:t

### Admin

Loggar in via `/login` med `APP_PASSWORD` och kan:

- skapa projekt
- generera och finslipa
- ladda ner projekt som ZIP
- ladda upp flera UTF-8-textfiler
- importera ZIP med projektfiler
- radera projekt
- se revisioner och interna leveransnoteringar

Filuppladdning skapar alltid en ny revision. Binära filer stöds inte i projektarkivet eftersom revisionerna lagras som text i databasen.

## Publik portfolio / rekryterarvy

Startsidan är utformad som en publik teknisk portfolio för Erica Nordlöf.
Den beskriver WebbBuilder Studio v2 som ett egenutvecklat AI-drivet system utan att felaktigt påstå att en egen språkmodell har tränats.

Den publika sidan lyfter bland annat:

- produktarkitektur runt OpenAI API
- FastAPI/Python och fullstackflödet
- bakgrundsjobb och statuspolling
- kvalitetskontroll och automatisk självreparation
- revisioner och preview
- publik besökarroll kontra privat adminroll
- Ericas fullstackbakgrund och kommande YH-studier till mjukvarutestare på EC Utbildning i Malmö

Besökare kan se projekt och previews. Adminbehörigheterna för skapande, AI-finslipning, uppladdning, nedladdning och radering är fortsatt låsta.


## Portfolio

Publik portfolio: https://webutvecklare.se/


## Publik GitHub-policy

WebbBuilder-repot ska inte länkas från den publika sidan. Repositoriet kan göras privat när projektet är färdigt. Den publika sidan ska i stället länka till https://webutvecklare.se/.

