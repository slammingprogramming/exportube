# Security Policy

Exportube processes sensitive personal data (YouTube watch history) and
talks to external services on your behalf. If you find a security issue --
anything from a privacy leak to a vulnerability in how data is handled,
stored, or transmitted -- please report it responsibly using the process
below rather than a public GitHub issue with details attached.

## Reporting a vulnerability

This project uses a two-step process: a public, detail-free notice to
start contact, followed by verified private communication over
[SimpleX Chat](https://simplex.chat/).

1. **Open a GitHub issue that states only that a security issue exists**
   and that you're reaching out because of its security implications --
   no technical details, no proof-of-concept, no affected-data specifics.
   This is just a knock on the door.
2. **Connect with the maintainer on SimpleX**:
   [smp14.simplex.im/a#3gZ-zeHs4QrFZKLAN0o3SC_XQJXhj1eYBVTO_c0FAtg](https://smp14.simplex.im/a#3gZ-zeHs4QrFZKLAN0o3SC_XQJXhj1eYBVTO_c0FAtg)
3. **Verify each other** with a public-key-signature exchange referencing
   the GitHub issue from step 1 -- this confirms the SimpleX contact and
   the GitHub reporter are the same person, in both directions, without
   either party needing to reveal anything beyond that.
4. Once verification succeeds both ways, the actual report and any
   further discussion happen privately over that SimpleX connection.

Why this process instead of just an email address: it lets a report be
attributed to a real, verifiable GitHub identity while keeping the
report's *content* off any public or centrally-logged channel, and it's a
one-time cost -- once you're verified, you can reach the maintainer
directly on SimpleX for any future report without repeating steps 1-3.

## Scope

In scope: this repository's code and documentation -- the acquisition
providers (Takeout parsing, YouTube session/API handling), the local
storage/cache layer, anything that determines what data is sent to
YouTube/MusicBrainz/Discogs or written to disk, and the review web UI.

Out of scope: vulnerabilities in third-party services this project talks
to (YouTube, MusicBrainz, Discogs, yt-dlp itself) -- please report those
upstream. See [docs/PRIVACY.md](docs/PRIVACY.md) for the full accounting
of what data goes where and to whom.

## Supported versions

This project is pre-1.0; security fixes land on the `main` branch and
there is currently no older release line receiving backports.
