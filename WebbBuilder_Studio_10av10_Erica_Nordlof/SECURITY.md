# Security model

WebbBuilder Studio hanterar kraftfulla integrationsnycklar och ska behandlas som ett privat administrativt verktyg.

## Principer

- Server-side secrets only.
- Privat repository som standard.
- Sandboxad preview.
- Ingen automatisk exekvering av genererad backend.
- Automatisk preflight före publicering.
- Publicering blockeras vid möjliga privata nycklar/API-hemligheter.
- Grundläggande säkerhetsheaders.
- Origin-kontroll för state-changing requests.
- Lösenordsskydd för buildern.

## Viktigt

`APP_PASSWORD` är ett enkelt single-user-skydd, inte full enterprise IAM.

För fler användare bör appen byggas ut med:

- riktig användardatabas,
- MFA,
- roller,
- CSRF-token per formulär,
- auditlogg,
- rate limiting,
- secret manager,
- PostgreSQL,
- central loggning.

## Genererad kod

AI-genererad kod kan innehålla logiska eller säkerhetsmässiga fel.

Kör därför inte okänd genererad backendkod med builderns egna credentials eller i samma trust boundary som buildern.
