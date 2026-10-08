# Official Addons

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/official-addons.md)

Addons extend a Ghost-ALICE OS install with optional capability packages on top of the core skills. This page is the contributor and operator reference for the addon sources surfaced on the homepage: official aliases, custom repositories, tenant packages, and local development addons. They all enter through the same installer entrypoint.

For the full install command surface, read [installation](../getting-started/installation.md). For how to package an addon you build or bring in, read the [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring).

## Contents

- [Official Addons](#official-addons-1)
- [Custom, Tenant, And Local Development Addons](#custom-tenant-and-local-development-addons)
- [Where To Go Next](#where-to-go-next)

## Official Addons

Official addons are maintained alongside the Ghost-ALICE core and are installed from the core checkout with a short alias. You do not need to know the repository URL; the alias resolves to the maintained source.

Install an official addon by passing its alias to the installer:

```bash
bash install.sh --addon autopilot
```

| Addon | Purpose | Install | Details |
| --- | --- | --- | --- |
| autopilot | Continue verified work items through the privileged autonomous adapter after explicit approval. | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |
| ai-secretary | Local desktop assistance with sender approval for outgoing communications and a replaceable OpenClaw runtime adapter. macOS only. | `bash install.sh --addon ai-secretary` | [AidALL/ghost-alice-ai-secretary](https://github.com/AidALL/ghost-alice-ai-secretary) |

The recommended product pair is Core `0.4.1` and Autopilot `0.4.1`. Autopilot's technical minimum remains Core `0.4.0` for the shared SQLite runtime. Check the addon compatibility matrix for support boundaries and the [current release notes](../release/2026-10-01-release-notes.md) for verification scope.

The Secretary alias selects source release `v0.1.0`. Its fixed installer provisions the complete owned bundle, including the pinned OpenClaw and Node runtimes, native desktop worker, skill copies and ordinary platform registry records. It does not start a gateway or install a model. Automatic platform detection aggregates Claude and Codex into one bundle installation; the generic skill copier does not publish a second Secretary copy. The shipped native worker requires macOS and Xcode command-line tools. Other hosts fail before bundle or core install changes.

For a local dependency cache, set `SECRETARY_OPENCLAW_SOURCE` to an existing npm prefix containing the pinned package and lockfile, and `SECRETARY_NODE_SOURCE` to a local distribution of the pinned Node version. Core forwards these two literal paths to the owned installer, which still checks version and integrity. These settings do not accept commands or additional arguments.

Remove the shared Secretary addon with `bash install.sh --platform codex --uninstall --addon ai-secretary` (or `claude` when that platform is installed). Removal delegates to the ownership-aware bundle uninstaller before core deletes any targets. Because the runtime is shared, this removes Secretary from every platform selected during its installation. User-modified resources and unrelated addons are preserved; unresolved ownership or drift stops removal for manual review. Mutable Secretary work state is retained.

Core's generic `--repair` preserves the complete Secretary bundle and its sidecars, and reports `use-owned-installer`. It does not copy a missing Secretary skill or rewrite one platform record. Inspect `status` through the installed Secretary installer, then use its ownership-aware uninstall/install with the original manifest parameters when recovery is needed.

An existing standalone installation using the root registry `~/.ghost-alice/addons`, or a different platform selection, needs an explicit migration. Run its installed `~/.local/share/ai-secretary/managed/app/scripts/installer.py uninstall` with the original `--prefix`, `--registry-dir` and repeated `--skills-dir` arguments recorded in `install-manifest.json`, then run the official alias. Core does not adopt or overwrite that existing ownership record. The [Secretary repository](https://github.com/AidALL/ghost-alice-ai-secretary) documents standalone recovery and installation details.

## Custom, Tenant, And Local Development Addons

When an addon is not an official alias, install it from an explicit path or URL with `--addon-source`. The source can be a local directory, a fork, a tenant-specific package, or a checkout you are developing.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

`--addon-source` also accepts a repository URL:

```bash
bash install.sh --addon-source https://github.com/your-org/your-addon.git
```

- Custom repositories: install an addon you maintain or fork by pointing `--addon-source` at its repository URL. The installer reads the addon manifest from that source instead of an official alias.
- Tenant packages: distribute an organization-specific or customer-specific addon bundle by pointing `--addon-source` at the tenant package location, so each tenant installs only the addons it is entitled to.
- Local development addons: while authoring an addon, point `--addon-source` at the working checkout on disk (for example `/path/to/addon-repo`) to install and re-test it without publishing first.

## Where To Go Next

- [Installation](../getting-started/installation.md): core install, official addons, custom addon sources, status, doctor, update, and platform selection.
- [Addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring): manifest format and packaging guidance for addons you build or bring in.
