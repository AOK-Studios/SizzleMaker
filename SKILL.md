# SizzleMaker skill

For agents: use this tool when a user wants a sizzle, recap or highlight video cut to music from a set of clips they own.

## Rules

- The clips folder or Drive link and the music file always come from the user running the tool. Never substitute stock or third party footage unless the user says to.
- Always run `plan` first and show the user the tempo and segment count before rendering.
- Run `sheets` when shot choice matters and let the user react to the contact sheets.
- Always finish with `audit`. If the audit does not pass, say so and fix the build. Do not hand over an unverified render.

## Commands

- Plan: `sizzlemaker plan --input-dir <folder> --music <file> [--drive-link <url>] [--beats-per-cut 4] [--start S] [--duration S]`
- Sheets: `sizzlemaker sheets --input-dir <folder>`
- Build: `sizzlemaker build --input-dir <folder> --music <file> --output <file.mp4> [--title TEXT] [--overlay-image PNG] [--voiceover FILE] [--vertical]`
- Audit: `sizzlemaker audit <file.mp4> [--expect-duration S] [--json]`

Exit codes: 0 means success. 3 means the audit failed on a built or checked file. Anything else is a usage or tool error, and the message on stderr says what is missing.
